"""Basic Windows SCM service wrapper for Frigate using pywin32.

pywin32 is part of docker/main/requirements-windows.txt, so a native install
already has the SCM modules this imports.
Install: scripts/install_frigate_service.ps1 -Action Install
Run:     python -m frigate.util.windows_service

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
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    IS_SERVICE = True
except ImportError:  # pragma: no cover - a Linux checkout has no pywin32
    pass


class FrigateService(win32serviceutil.ServiceFramework):
    """Windows SCM service that runs Frigate natively."""

    _svc_name_ = "Frigate"
    _svc_display_name_ = "Frigate NVR (Windows Native)"
    _svc_description_ = (
        "Realtime object detection NVR for IP cameras — native Windows port."
    )

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
        import logging
        import os
        import signal
        import subprocess

        # Session 0 has no console: SIGINT / SIGBREAK do not exist.
        # Register only the signals that can actually arrive.
        try:
            signal.signal(signal.SIGTERM, lambda s, f: self.SvcStop())
        except (ValueError, OSError):
            pass

        # Frigate's own entry point rather than the launcher: a .ps1 cannot be
        # started by Popen, and the service already runs in the venv the
        # launcher would have activated.
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
            win32event.WaitForSingleObject(self.hWaitStop, 500)  # 500ms polling
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
    if not IS_SERVICE:
        print("pywin32 is not installed, so the SCM service cannot run.")
        print("It is part of the native Windows requirements:")
        print("    pip install -r docker/main/requirements-windows.txt")
        print("Then install the service with:")
        print("    scripts/install_frigate_service.ps1 -Action Install")
        sys.exit(1)

    if len(sys.argv) == 1:
        # started by the service control manager: the dispatcher is what tells
        # the SCM that this process is the service it launched. Without it a
        # service start gets an argument-less command line parser instead.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(FrigateService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # install, update, remove or debug from a command line
        win32serviceutil.HandleCommandLine(FrigateService)
