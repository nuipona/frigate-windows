"""Basic Windows SCM service wrapper for Frigate using pywin32.

Requires: pip install pywin32
Usage (install): python -m pywin32_framework install --name Frigate --display "Frigate NVR"
Usage (run):    python -m frigate.util.windows_service

This is a minimal service framework. It installs signal handlers that work
in session 0 (no console events available) and delegates to Frigate's main
entry point through a subprocess launcher so the service manager can restart
cleanly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_SERVICE = False

try:
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager

    IS_SERVICE = True
except ImportError:  # pragma: no cover - pywin32 is optional
    pass


class FrigateService(win32serviceutil.ServiceFramework):
    """Windows SCM service that runs Frigate natively."""

    _svc_name_ = "Frigate"
    _svc_display_name_ = "Frigate NVR (Windows Native)"
    _svc_description_ = "Realtime object detection NVR for IP cameras — native Windows port."

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.stop_requested = False

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_requested = True
        # Signal graceful shutdown to Frigate via the restart/stop event mechanism.
        # The launcher (run_frigate.ps1) reads the restart marker and exits cleanly.
        restart_marker = Path(os.environ.get("LOCALAPPDATA", ".")) / "Frigate" / "tmp"
        restart_marker.mkdir(parents=True, exist_ok=True)
        (restart_marker / "restart.requested").write_text("service_stop")
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self) -> None:
        import signal
        import os
        import subprocess
        import logging
        from frigate.util.services import restart_frigate

        # Session 0 has no console: SIGINT / SIGBREAK do not exist.
        # Register only the signals that can actually arrive.
        try:
            signal.signal(signal.SIGTERM, lambda s, f: self.SvcStop())
        except (ValueError, OSError):
            pass

        # Use the launcher script so all Windows-specific setup (PATH, venv)
        # is reused rather than re-implemented here.
        launcher = Path(__file__).resolve().parent.parent.parent / "run_frigate.ps1"
        # Alternatively, run python -m frigate directly:
        cmd = [sys.executable, "-m", "frigate"]
        env = os.environ.copy()
        env["FRIGATE_SERVICE_MODE"] = "1"

        logger = logging.getLogger("frigate_service")
        logger.info("Starting Frigate service with command: %s", cmd)

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
            env=env,
        )

        # Wait for SCM stop event or process exit.
        while not self.stop_requested:
            result = win32event.WaitForSingleObject(
                self.hWaitStop, 500
            )  # 500ms polling
            ret = process.poll()
            if ret is not None:
                logger.info("Frigate process exited with code %d", ret)
                break
            if self.stop_requested:
                break

        # Graceful shutdown: ask ffmpeg via stdin (q), then terminate.
        if process.poll() is None:
            try:
                process.stdin.write(b"q\n")  # type: ignore[attr-defined]
                process.stdin.flush()  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                process.kill()

        self.ReportServiceStatus(win32service.SERVICE_STOPPED)


if __name__ == "__main__":
    if IS_SERVICE:
        win32serviceutil.HandleCommandLine(FrigateService)
    else:
        print("pywin32 is not installed. To use this service:")
        print("    pip install pywin32")
        print("Then install with:")
        print("    python -c \"import frigate.util.windows_service; from frigate.util.windows_service import FrigateService; import win32serviceutil; win32serviceutil.InstallService(FrigateService)\"")
        sys.exit(1)
