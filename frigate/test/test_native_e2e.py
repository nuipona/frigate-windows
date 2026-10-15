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
            # Each test gets an isolated tree. Reusing one base means the
            # service-stop test can see recordings from the preceding test and
            # stop before its own camera has written a segment.
            self.base = os.path.abspath(os.path.join(pinned, self._testMethodName))
            shutil.rmtree(self.base, ignore_errors=True)
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
        candidates = (
            Path(self.base) / "tmp" / "logs" / "frigate" / "current",
            Path(self.log_path),
        )

        for path in candidates:
            try:
                with open(path, encoding="utf-8", errors="replace") as log:
                    content = log.readlines()
                    if content:
                        return "".join(content[-lines:])
            except OSError:
                continue

        return "<no log>"

    def _run_service_action(
        self, action: str, timeout: float = 120
    ) -> sp.CompletedProcess:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(REPO_ROOT) / "scripts" / "install_frigate_service.ps1"),
            "-Action",
            action,
        ]

        if action == "Install":
            command.extend(["-BaseDir", self.base, "-PythonPath", sys.executable])

        return sp.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _service_is_admin(self) -> bool:
        result = sp.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$identity = [Security.Principal.WindowsIdentity]::GetCurrent(); "
                "$principal = [Security.Principal.WindowsPrincipal]::new($identity); "
                "$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def _service_is_registered(self) -> bool:
        result = sp.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "if (Get-Service -Name Frigate -ErrorAction SilentlyContinue) "
                "{ 'present' } else { 'absent' }",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Unable to query the Frigate service: {result.stderr}\n{result.stdout}"
            )
        return result.stdout.strip().lower() == "present"

    def _service_start_name(self) -> str | None:
        result = sp.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance -ClassName Win32_Service "
                "-Filter \"Name='Frigate'\").StartName",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _service_status(self) -> str | None:
        result = sp.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-Service -Name Frigate -ErrorAction Stop).Status.ToString()",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _remove_installed_service(self) -> None:
        if not getattr(self, "_service_remove_pending", False):
            return

        try:
            result = self._run_service_action("Remove", timeout=60)
        except (OSError, sp.TimeoutExpired) as err:
            logger.error("Unable to remove the E2E Frigate service: %s", err)
            return

        if result.returncode != 0:
            logger.error(
                "Unable to remove the E2E Frigate service: %s\n%s",
                result.stdout,
                result.stderr,
            )
        else:
            self._service_remove_pending = False

    def _start_frigate(self, service_stop_event: str | None = None) -> None:
        env = dict(os.environ)
        env["FRIGATE_BASE_DIR"] = self.base

        if service_stop_event is not None:
            # exactly what the SCM service passes to the Frigate it starts: the
            # service mode flag, and the named event it will set to ask for a
            # clean stop
            env["FRIGATE_SERVICE_MODE"] = "1"
            env["FRIGATE_SERVICE_STOP_EVENT"] = service_stop_event
        else:
            env.pop("FRIGATE_SERVICE_MODE", None)
            env.pop("FRIGATE_SERVICE_STOP_EVENT", None)

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

        self._assert_stopped_cleanly(exit_code)

    @unittest.skipUnless(os.name == "nt", "a named event is a Windows mechanism")
    def test_service_stop_event_shuts_down_cleanly(self) -> None:
        """The service stops Frigate by setting a named event.

        A service cannot deliver a console control event to its child in
        session 0, and terminating the process would leave the segment being
        written without a moov atom, so the service asks through an event
        instead. This starts Frigate the way the service does and then does
        what the service does, so the whole stop path is exercised the way the
        SCM will exercise it.
        """
        try:
            import win32event
        except ImportError:  # pragma: no cover - pywin32 is a Windows requirement
            self.skipTest("pywin32 is required to signal the stop event")

        name = f"FrigateE2EStop-{os.getpid()}"
        handle = win32event.CreateEvent(None, 1, 0, name)
        self.addCleanup(handle.Close)

        self._start_frigate(service_stop_event=name)

        ready = _wait_until(self._api_ready, timeout=120)
        self.assertTrue(
            ready,
            "Frigate never answered /api/version with a service stop event.\n"
            + self._log_tail(),
        )

        recorded = _wait_until(lambda: len(self._segments()) >= 1, timeout=180)
        self.assertTrue(
            recorded,
            "No recording segments appeared before the stop.\n"
            + self._cache_listing()
            + self._camera_stats()
            + self._log_tail(),
        )

        # what the SCM asks the service to do on stop
        win32event.SetEvent(handle)

        try:
            exit_code = self.proc.wait(timeout=120)
        except sp.TimeoutExpired:
            self.proc.kill()
            self.fail(
                "Frigate did not exit after its stop event was set.\n"
                + self._log_tail()
            )

        self._assert_stopped_cleanly(exit_code)

    @unittest.skipUnless(
        os.name == "nt", "the Windows SCM is only available on Windows"
    )
    def test_scm_service_installs_and_stops_cleanly(self) -> None:
        """Exercise registration, service-account startup and an SCM stop.

        This is deliberately separate from the named-event E2E: that test
        proves Frigate responds to the event, while this one verifies the
        installer and the actual Service Control Manager path that creates
        and sets it.
        """
        if os.environ.get("FRIGATE_E2E_SCM", "").strip() != "1":
            self.skipTest("set FRIGATE_E2E_SCM=1 to install a temporary SCM service")

        self.assertTrue(
            self._service_is_admin(),
            "FRIGATE_E2E_SCM=1 requires an elevated Windows runner",
        )
        if self._service_is_registered():
            self.skipTest("the Frigate SCM service is already registered")

        # Register cleanup before installation so even a partially completed
        # install is removed before the isolated data directory is deleted.
        self._service_remove_pending = True
        self.addCleanup(self._remove_installed_service)

        installed = self._run_service_action("Install")
        self.assertEqual(
            installed.returncode,
            0,
            "Service installation failed.\n"
            + installed.stdout
            + installed.stderr
            + self._log_tail(),
        )

        self.assertEqual(
            (self._service_start_name() or "").casefold(),
            r"NT AUTHORITY\LocalService".casefold(),
            "The SCM service must run as LocalService",
        )

        ready = _wait_until(self._api_ready, timeout=180)
        self.assertTrue(
            ready,
            "The SCM service never brought Frigate's API up.\n" + self._log_tail(),
        )

        recorded = _wait_until(lambda: len(self._segments()) >= 1, timeout=180)
        self.assertTrue(
            recorded,
            "The SCM service did not produce a saved recording.\n"
            + self._cache_listing()
            + self._camera_stats()
            + self._log_tail(),
        )

        stopped = self._run_service_action("Stop", timeout=90)
        self.assertEqual(
            stopped.returncode,
            0,
            "The SCM service rejected the stop request.\n"
            + stopped.stdout
            + stopped.stderr
            + self._log_tail(),
        )
        service_stopped = _wait_until(
            lambda: self._service_status() == "Stopped", timeout=90
        )
        self.assertTrue(
            service_stopped,
            f"The SCM did not report Frigate stopped (status: {self._service_status()}).\n"
            + self._log_tail(),
        )

        # The service host does not expose its child's exit code. These checks
        # prove that FrigateApp.stop ran and that user-visible output is intact.
        self._assert_stopped_cleanly(None)

        removed = self._run_service_action("Remove")
        self.assertEqual(
            removed.returncode,
            0,
            "The SCM service could not be removed after a clean stop.\n"
            + removed.stdout
            + removed.stderr,
        )
        removed_from_scm = _wait_until(
            lambda: not self._service_is_registered(), timeout=20
        )
        self.assertTrue(removed_from_scm, "The removed service is still registered")
        self._service_remove_pending = False

    def _assert_stopped_cleanly(self, exit_code: int | None) -> None:
        """Everything that has to be true of a shutdown rather than a death."""
        self.log_file.flush()

        # FrigateApp.stop() writes this before doing anything else. A process
        # that was terminated rather than signalled never gets there.
        stopping_flag = Path(self.base) / "tmp" / ".frigate-is-stopping"
        self.assertTrue(
            stopping_flag.exists(),
            "Frigate exited without running FrigateApp.stop(); it was killed "
            "rather than shut down.\n" + self._log_tail(),
        )

        if exit_code is not None:
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

        # Only files moved into recordings are user-visible output. The cache
        # is staging space and can contain the partial segment that ffmpeg was
        # writing when Frigate stopped; the next run validates or discards it.
        recordings_dir = Path(self.base) / "media" / "recordings"
        broken = [
            str(path)
            for path in recordings_dir.rglob("*.mp4")
            if not self._probe_ok(str(path))
        ]
        self.assertEqual(
            broken,
            [],
            "These recorded files were left incomplete, which is what "
            "happens when ffmpeg is killed instead of stopped:\n" + "\n".join(broken),
        )


if __name__ == "__main__":
    unittest.main()
