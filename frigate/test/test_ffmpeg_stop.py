"""Tests for stopping ffmpeg without killing it.

``Popen.terminate()`` is SIGTERM on POSIX, which ffmpeg handles by finalizing
the file it is writing. On Windows it is ``TerminateProcess``, so an MP4
segment that has not been closed yet is left without a moov atom and cannot be
played. :func:`frigate.util.ffmpeg.request_ffmpeg_stop` is where that
difference is handled, and these tests cover each branch of it.
"""

import logging
import signal
import subprocess as sp
import unittest
from unittest.mock import MagicMock, patch

from frigate.util import ffmpeg as ffmpeg_util

logger = logging.getLogger(__name__)


def _process(stdin=None, wait_side_effect=None) -> MagicMock:
    process = MagicMock()
    process.stdin = stdin
    process.pid = 4242
    process.wait.side_effect = wait_side_effect
    return process


class TestRequestFfmpegStop(unittest.TestCase):
    @patch.object(ffmpeg_util, "IS_WINDOWS", False)
    def test_posix_just_terminates(self):
        process = _process(stdin=MagicMock())

        with patch.object(ffmpeg_util.os, "kill") as mock_kill:
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()
        process.stdin.write.assert_not_called()
        mock_kill.assert_not_called()

    @patch.object(ffmpeg_util, "IS_WINDOWS", True)
    def test_windows_writes_the_quit_command_and_waits(self):
        stdin = MagicMock()
        process = _process(stdin=stdin)

        with patch.object(ffmpeg_util.os, "kill") as mock_kill:
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        stdin.write.assert_called_once_with(b"q")
        stdin.flush.assert_called_once()
        process.wait.assert_called_once_with(timeout=ffmpeg_util.FFMPEG_QUIT_GRACE)
        process.terminate.assert_not_called()
        mock_kill.assert_not_called()

    @patch.object(ffmpeg_util, "IS_WINDOWS", True)
    def test_windows_falls_back_to_a_break_event(self):
        """A break event is aimed at the process group, never a CTRL_C_EVENT.

        GenerateConsoleCtrlEvent drops CTRL_C_EVENT when a process group is
        named, so only the break event can be targeted at one ffmpeg process.
        """
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 1))

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill") as mock_kill,
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        mock_kill.assert_called_once_with(4242, 21)
        process.terminate.assert_not_called()

    @patch.object(ffmpeg_util, "IS_WINDOWS", True)
    def test_windows_falls_back_to_terminate_without_a_break_event(self):
        """No console, as when Frigate runs as a Windows service."""
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 1))

        ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()

    @patch.object(ffmpeg_util, "IS_WINDOWS", True)
    def test_windows_falls_back_to_terminate_when_stdin_is_gone(self):
        stdin = MagicMock()
        stdin.write.side_effect = BrokenPipeError("gone")
        process = _process(stdin=stdin)

        ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()

    @patch.object(ffmpeg_util, "IS_WINDOWS", True)
    def test_windows_falls_back_to_terminate_when_the_break_event_fails(self):
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 1))

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill", side_effect=OSError("no console")),
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()

    @patch.object(ffmpeg_util, "IS_WINDOWS", True)
    def test_windows_without_a_stdin_goes_straight_to_the_break_event(self):
        process = _process(stdin=None)

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill") as mock_kill,
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        mock_kill.assert_called_once_with(4242, 21)
        process.terminate.assert_not_called()


class TestStopFfmpeg(unittest.TestCase):
    @patch.object(ffmpeg_util, "IS_WINDOWS", False)
    def test_stops_and_reaps_the_process(self):
        process = MagicMock()

        ffmpeg_util.stop_ffmpeg(process, logger)

        process.terminate.assert_called_once()
        process.communicate.assert_called_once_with(
            timeout=ffmpeg_util.FFMPEG_STOP_TIMEOUT
        )
        process.kill.assert_not_called()

    @patch.object(ffmpeg_util, "IS_WINDOWS", False)
    def test_kills_a_process_that_will_not_stop(self):
        process = MagicMock()
        process.communicate.side_effect = [sp.TimeoutExpired("ffmpeg", 1), None]

        ffmpeg_util.stop_ffmpeg(process, logger)

        process.kill.assert_called_once()


if __name__ == "__main__":
    unittest.main()
