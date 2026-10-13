"""End to end test for a native (non container) Frigate install.

This runs the real ``python -m frigate`` against a synthetic camera, waits for
recordings to appear, asks Frigate to stop the way the UI does, and then checks
what is left behind. It exists because every bug in this area is about what
happens *between* processes, which is exactly what unit tests cannot see:

- a Frigate that is killed rather than signalled never writes its stopping
  flag, because ``FrigateApp.stop()`` never runs
- ffmpeg processes that are killed rather than stopped leave an MP4 segment
  with no moov atom, which cannot be played
- child processes that outlive Frigate hold the IPC ports, so the next start
  cannot bind them

Opt in with ``FRIGATE_E2E=1``. The test is skipped otherwise because it needs
ffmpeg and ffprobe on PATH, starts real processes, and takes tens of seconds.

Every failure dumps the tail of Frigate's own log, so a broken run on CI says
what went wrong rather than just timing out.
"""

import json
import logging
import os
import shutil
import socket
import subprocess as sp
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RUN = os.environ.get("FRIGATE_E2E", "").strip().lower() in ("1", "true", "yes")

#: where the test finds the checkout, so `python -m frigate` imports this tree
REPO_ROOT = str(Path(__file__).resolve().parents[2])

#: a single camera on a synthetic source, recording only. Detection is off so
#: the test does not depend on a real model, and hardware acceleration is off
#: so it does not depend on a GPU or on go2rtc being reachable.
#:
#: `-re` is what makes the synthetic source behave like a camera. lavfi has no
#: clock: without it ffmpeg decodes as fast as it can, which is hundreds of
#: frames a second written into the detect pipe that Frigate reads at the
#: camera's frame rate, and the frames that do get through are torn.
CONFIG_TEMPLATE = """\
mqtt:
  enabled: false

networking:
  listen:
    internal: {port}

ffmpeg:
  hwaccel_args: []

cameras:
  e2e:
    ffmpeg:
      inputs:
        - path: "testsrc=size=320x240:rate=10"
          input_args: "-f lavfi -re"
          roles:
            - record
      output_args:
        record: >-
          -f segment -segment_time 5 -segment_format mp4
          -reset_timestamps 1 -strftime 1
          -c:v libx264 -preset ultrafast -g 25 -an
    detect:
      enabled: false
      width: 320
      height: 240
      fps: 10
    record:
      enabled: true
      continuous:
        days: 1
      motion:
        days: 1
    snapshots:
      enabled: false
    motion:
      enabled: false
    objects:
      track: []
"""

#: process names that must not survive Frigate
CHILD_NAMES = ("ffmpeg", "go2rtc")

logger = logging.getLogger(__name__)


def _free_port() -> int:
    """A port that was free a moment ago."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until(predicate, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@unittest.skipUnless(RUN, "set FRIGATE_E2E=1 to run the native end to end test")
class TestNativeEndToEnd(unittest.TestCase):
    """Boot Frigate, record, stop it, and check what is left behind."""

    proc: sp.Popen | None = None
    base: str = ""
    keep: bool = False

    def setUp(self) -> None:
        missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
        if missing:
            self.skipTest(f"{', '.join(missing)} must be on PATH")

        # FRIGATE_E2E_DIR pins the location and keeps it afterwards, which is
        # what makes the logs uploadable when this fails on CI
        pinned = os.environ.get("FRIGATE_E2E_DIR", "").strip()
        keep = os.environ.get("FRIGATE_E2E_KEEP", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

        if pinned:
            self.base = os.path.abspath(pinned)
            os.makedirs(self.base, exist_ok=True)
        else:
            self.base = tempfile.mkdtemp(prefix="frigate-e2e-")

        self.keep = bool(pinned) or keep
        self.port = _free_port()
        self.addCleanup(self._cleanup)

        config_path = os.path.join(self.base, "config", "config.yml")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as config_file:
            config_file.write(CONFIG_TEMPLATE.format(port=self.port))

        self.log_path = os.path.join(self.base, "frigate-run.log")
        self.log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")

        self._snapshot_children()

    def _cleanup(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except sp.TimeoutExpired:
                pass
        self.proc = None

        if getattr(self, "log_file", None) is not None:
            self.log_file.close()

        if self.base and not self.keep and os.path.isdir(self.base):
            shutil.rmtree(self.base, ignore_errors=True)

    def _log_tail(self, lines: int = 60) -> str:
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as log:
                return "".join(log.readlines()[-lines:])
        except OSError:
            return "<no log>"

    def _start_frigate(self) -> None:
        env = dict(os.environ)
        env["FRIGATE_BASE_DIR"] = self.base
        for name in (
            "FRIGATE_CONFIG_DIR",
            "FRIGATE_MEDIA_DIR",
            "FRIGATE_CACHE_DIR",
            "FRIGATE_TMP_DIR",
            "CONFIG_FILE",
        ):
            env.pop(name, None)

        kwargs: dict = {
            "env": env,
            "cwd": REPO_ROOT,
            "stdout": self.log_file,
            "stderr": sp.STDOUT,
        }

        if os.name == "nt":
            # its own process group, so a break event can be aimed at it
            kwargs["creationflags"] = sp.CREATE_NEW_PROCESS_GROUP

        self.proc = sp.Popen([sys.executable, "-m", "frigate"], **kwargs)

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _api_ready(self) -> bool:
        if self.proc is not None and self.proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(self._url("/api/version"), timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _segments(self) -> list[str]:
        recordings = Path(self.base) / "media" / "recordings"
        if not recordings.is_dir():
            return []
        return [str(path) for path in recordings.rglob("*.mp4")]

    def _probe_ok(self, path: str) -> bool:
        """Whether ffprobe can open the file and read a duration from it.

        An MP4 that ffmpeg was killed mid-segment has no moov atom, so ffprobe
        refuses to open it at all.
        """
        result = sp.run(
            [
                shutil.which("ffprobe") or "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        try:
            return float(result.stdout.strip()) > 0
        except ValueError:
            return False

    def _cache_listing(self) -> str:
        """What ffmpeg has written so far, and therefore where to look next.

        Segments are written into the cache first and only moved into
        recordings afterwards, so an empty recordings directory means
        something very different depending on what the cache holds: nothing at
        all means ffmpeg never wrote a segment, a pile of mp4 files means the
        mover never picked them up.
        """
        cache = Path(self.base) / "cache"
        if not cache.is_dir():
            return f"there is no cache directory at {cache}\n"

        segments = sorted(path.name for path in cache.rglob("*.mp4"))
        if not segments:
            return f"there are no segments in {cache}\n"

        return (
            f"{len(segments)} segments are sitting in the cache: "
            f"{', '.join(segments[:8])}\n"
        )

    def _camera_stats(self) -> str:
        """The counters that say whether any frame moved at all.

        Nothing downstream of the capture process can be trusted if these
        are zero, and the recording maintainer keeps the cache empty by
        discarding segments it has no processed frame for.
        """
        try:
            with urllib.request.urlopen(self._url("/api/stats"), timeout=5) as resp:
                stats = json.load(resp)
        except (urllib.error.URLError, OSError, ValueError) as err:
            return f"could not read /api/stats: {err}\n"

        camera = stats.get("cameras", {}).get("e2e", {})
        counters = ("camera_fps", "process_fps", "detection_fps", "skipped_fps")

        return (
            "e2e camera: "
            + ", ".join(f"{name}={camera.get(name)}" for name in counters)
            + "\n"
        )

    def _snapshot_children(self) -> None:
        """Remember the ffmpeg processes that were already running.

        The runner can have ffmpeg processes of its own, so orphans are
        identified as the ones that were not there before Frigate started.
        """
        self._baseline: set[int] = set()
        try:
            import psutil
        except ImportError:  # pragma: no cover - psutil is a hard dependency
            return

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if any(child in name for child in CHILD_NAMES):
                self._baseline.add(proc.pid)

    def _orphans(self) -> list[str]:
        """Processes Frigate started that are still running after it exited."""
        try:
            import psutil
        except ImportError:  # pragma: no cover - psutil is a hard dependency
            return []

        orphans = []
        for proc in psutil.process_iter(["name", "ppid"]):
            try:
                if proc.pid in self._baseline:
                    continue
                name = (proc.info.get("name") or "").lower()
                if any(child in name for child in CHILD_NAMES):
                    orphans.append(f"{name} (pid {proc.pid}, ppid {proc.info['ppid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return orphans

    def test_boot_records_and_stops_cleanly(self) -> None:
        self._start_frigate()

        ready = _wait_until(self._api_ready, timeout=120)
        self.assertTrue(
            ready,
            "Frigate never answered /api/version.\n" + self._log_tail(),
        )

        # two segments means the segment muxer rolled over at least once, so a
        # complete file has been through the cache and into recordings
        recorded = _wait_until(lambda: len(self._segments()) >= 2, timeout=180)
        self.assertTrue(
            recorded,
            "No recording segments appeared under media/recordings.\n"
            + self._cache_listing()
            + self._camera_stats()
            + self._log_tail(),
        )

        # stop Frigate the way the UI does
        request = urllib.request.Request(self._url("/api/restart"), data=b"{}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                self.assertIn(resp.status, (200, 204))
        except (urllib.error.URLError, OSError) as err:
            # Frigate can close the connection on its way down. The API was
            # already proven reachable above, so what matters is that the
            # process exits cleanly, which is what the assertions below check.
            logger.debug("/api/restart did not produce a response: %s", err)

        try:
            exit_code = self.proc.wait(timeout=120)
        except sp.TimeoutExpired:
            self.proc.kill()
            self.fail("Frigate did not exit after /api/restart.\n" + self._log_tail())

        self.log_file.flush()

        # FrigateApp.stop() writes this before doing anything else. A process
        # that was terminated rather than signalled never gets there.
        stopping_flag = Path(self.base) / "tmp" / ".frigate-is-stopping"
        self.assertTrue(
            stopping_flag.exists(),
            "Frigate exited without running FrigateApp.stop(); it was killed "
            "rather than shut down.\n" + self._log_tail(),
        )

        self.assertEqual(
            exit_code,
            0,
            f"Frigate exited with {exit_code} rather than 0.\n" + self._log_tail(),
        )

        # give the operating system a moment to reap what the job object or
        # the process group is responsible for
        _wait_until(lambda: not self._orphans(), timeout=30)
        orphans = self._orphans()
        self.assertEqual(
            orphans,
            [],
            "Child processes outlived Frigate, so its IPC ports stay bound "
            "and the next start cannot bind them: "
            + ", ".join(orphans)
            + "\n"
            + self._log_tail(),
        )

        # anything still on disk has to be a complete file
        broken = [
            str(path)
            for path in Path(self.base).rglob("*.mp4")
            if not self._probe_ok(str(path))
        ]
        self.assertEqual(
            broken,
            [],
            "These recording files were left incomplete, which is what "
            "happens when ffmpeg is killed instead of stopped:\n" + "\n".join(broken),
        )


if __name__ == "__main__":
    unittest.main()
