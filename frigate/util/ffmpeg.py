"""FFmpeg utility functions for managing ffmpeg processes."""

import logging
import os
import signal
import subprocess as sp
from collections.abc import Callable
from typing import Any

from frigate.const import PROCESS_PRIORITY_LOW
from frigate.log import LogPipe
from frigate.util.platform import IS_WINDOWS, get_process_priority_class

#: how long to wait for ffmpeg after asking it to stop before killing it
FFMPEG_STOP_TIMEOUT = 30

#: how long ffmpeg gets to close the file it is writing after a quit command
FFMPEG_QUIT_GRACE = 10.0


def request_ffmpeg_stop(ffmpeg_process: sp.Popen[Any], logger: logging.Logger) -> None:
    """Ask an ffmpeg process to exit, letting it finish the file it is writing.

    On POSIX this is ``terminate()``: SIGTERM, which ffmpeg handles by closing
    and finalizing whatever it has open.

    On Windows ``terminate()`` is ``TerminateProcess``, so an MP4 segment that
    has not been closed yet is left without a moov atom and cannot be played.
    Windows therefore gets the two mechanisms that let ffmpeg exit by itself:

    1. the ``q`` command on stdin, which ffmpeg reads even when stdin is a
       pipe rather than a terminal
    2. a ``CTRL_BREAK_EVENT`` into the process group ``start_new_session``
       created, which ffmpeg's console handler responds to by shutting down
       cleanly

    Both are best effort. Anything that goes wrong leaves ``terminate()`` as
    the fallback, so the worst case is what already happens today.
    """
    if not IS_WINDOWS:
        ffmpeg_process.terminate()
        return

    # 1. quit command on stdin
    if ffmpeg_process.stdin is not None:
        try:
            ffmpeg_process.stdin.write(b"q")
            ffmpeg_process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as err:
            logger.debug("Could not send ffmpeg the quit command: %s", err)
        else:
            try:
                ffmpeg_process.wait(timeout=FFMPEG_QUIT_GRACE)
                return
            except sp.TimeoutExpired:
                logger.debug("ffmpeg is still running after the quit command")

    # 2. break event into the process group. CTRL_BREAK_EVENT can be directed
    #    at a single process group, unlike CTRL_C_EVENT, which is silently
    #    dropped when one is named. It does need a console, so a Frigate
    #    running as a Windows service falls through to terminate().
    ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)

    if ctrl_break is not None:
        try:
            os.kill(ffmpeg_process.pid, ctrl_break)
            return
        except (OSError, ValueError) as err:
            logger.debug("Could not send ffmpeg a break event: %s", err)

    ffmpeg_process.terminate()


def stop_ffmpeg(ffmpeg_process: sp.Popen[Any], logger: logging.Logger) -> None:
    logger.info("Terminating the existing ffmpeg process...")
    request_ffmpeg_stop(ffmpeg_process, logger)
    try:
        logger.info("Waiting for ffmpeg to exit gracefully...")
        ffmpeg_process.communicate(timeout=FFMPEG_STOP_TIMEOUT)
        logger.info("FFmpeg has exited")
    except sp.TimeoutExpired:
        logger.info("FFmpeg didn't exit. Force killing...")
        ffmpeg_process.kill()
        ffmpeg_process.communicate()
        logger.info("FFmpeg has been killed")
    ffmpeg_process = None


def terminate_ffmpeg_stream(proc: sp.Popen[Any]) -> None:
    """Stop an ffmpeg process whose stdout is being read over a pipe."""
    # Close the read end first so a blocked ffmpeg write unblocks (ffmpeg then
    # sees a broken pipe), then signal it. The resulting ffmpeg write error is
    # harmless and goes to the captured stderr.
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass
    if proc.poll() is None:
        request_ffmpeg_stop(proc, logger)
        try:
            proc.wait(timeout=5)
        except sp.TimeoutExpired:
            proc.kill()
            proc.wait()


def start_or_restart_ffmpeg(
    ffmpeg_cmd, logger, logpipe: LogPipe, frame_size=None, ffmpeg_process=None
) -> sp.Popen[Any]:
    if ffmpeg_process is not None:
        stop_ffmpeg(ffmpeg_process, logger)

        # flush after the stop so the logs cover ffmpeg's output up to exit
        logpipe.dump()

    # stdin is a pipe rather than /dev/null so that request_ffmpeg_stop can
    # write ffmpeg's quit command to it. ffmpeg never reads stdin for an input
    # unless "-" is the source, so an unwritten pipe costs nothing.
    if frame_size is None:
        process = sp.Popen(
            ffmpeg_cmd,
            stdout=sp.DEVNULL,
            stderr=logpipe,
            stdin=sp.PIPE,
            start_new_session=True,
        )
    else:
        process = sp.Popen(
            ffmpeg_cmd,
            stdout=sp.PIPE,
            stderr=logpipe,
            stdin=sp.PIPE,
            bufsize=frame_size * 10,
            start_new_session=True,
        )
    return process


logger = logging.getLogger(__name__)


def inject_progress_flags(cmd: list[str]) -> list[str]:
    """Insert `-progress pipe:2 -nostats` immediately before the output path.

    `-progress pipe:2` writes structured key=value lines to stderr;
    `-nostats` suppresses the noisy default stats output. The output path
    is conventionally the last token in an FFmpeg argv.
    """
    if not cmd:
        return cmd
    return cmd[:-1] + ["-progress", "pipe:2", "-nostats", cmd[-1]]


def run_ffmpeg_with_progress(
    cmd: list[str],
    *,
    expected_duration_seconds: float,
    on_progress: Callable[[float], None] | None = None,
    stdin_payload: str | None = None,
    process_started: Callable[[sp.Popen], None] | None = None,
    use_low_priority: bool = True,
) -> tuple[int, str]:
    """Run an ffmpeg command, streaming progress via `-progress pipe:2`.

    Args:
        cmd: ffmpeg argv. Output path must be the last token.
        expected_duration_seconds: Duration of the expected output clip in
            seconds. Used to convert ffmpeg's `out_time_us` into a percent.
        on_progress: Optional callback invoked with a percent in [0, 100].
            Called once with 0.0 at start, again on each `out_time_us=`
            stderr line, and once with 100.0 on `progress=end`.
        stdin_payload: Optional string written to ffmpeg stdin (used by
            export for concat playlists).
        process_started: Optional callback invoked with the live `Popen`
            once spawned — lets callers store the ref for cancellation.
        use_low_priority: When True, run FFmpeg at low process priority so
            concat doesn't starve detection. Uses `nice` on POSIX and a Windows
            process priority class on Windows.

    Returns:
        Tuple of `(returncode, captured_stderr)`. Stdout is left attached
        to the parent process to avoid buffer-full deadlocks.
    """
    full_cmd = inject_progress_flags(cmd)
    popen_kwargs: dict[str, Any] = {}
    if use_low_priority:
        if IS_WINDOWS:
            priority_class = get_process_priority_class(PROCESS_PRIORITY_LOW)
            if priority_class is not None:
                popen_kwargs["creationflags"] = priority_class
        else:
            full_cmd = ["nice", "-n", str(PROCESS_PRIORITY_LOW)] + full_cmd

    def emit(percent: float) -> None:
        if on_progress is None:
            return
        try:
            on_progress(max(0.0, min(100.0, percent)))
        except Exception:
            logger.exception("FFmpeg progress callback failed")

    emit(0.0)

    proc = sp.Popen(
        full_cmd,
        stdin=sp.PIPE if stdin_payload is not None else None,
        stderr=sp.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    if process_started is not None:
        try:
            process_started(proc)
        except Exception:
            logger.exception("FFmpeg process_started callback failed")

    if stdin_payload is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_payload)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    captured: list[str] = []
    if proc.stderr is not None:
        try:
            for raw_line in proc.stderr:
                captured.append(raw_line)
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("out_time_us="):
                    if expected_duration_seconds <= 0:
                        continue
                    try:
                        out_time_us = int(line.split("=", 1)[1])
                    except (ValueError, IndexError):
                        continue
                    if out_time_us < 0:
                        continue
                    out_seconds = out_time_us / 1_000_000.0
                    emit((out_seconds / expected_duration_seconds) * 100.0)
                elif line == "progress=end":
                    emit(100.0)
                    break
        except Exception:
            logger.exception("Failed reading FFmpeg progress stream")

    proc.wait()

    if proc.stderr is not None:
        try:
            remaining = proc.stderr.read()
            if remaining:
                captured.append(remaining)
        except Exception:
            pass

    return proc.returncode or 0, "".join(captured)
