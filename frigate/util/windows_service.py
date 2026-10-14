"""Windows SCM service wrapper for Frigate, using pywin32.

pywin32 is part of docker/main/requirements-windows.txt, so a native install
already has the SCM modules this imports.
Install: scripts/install_frigate_service.ps1 -Action Install
Run:     python -m frigate.util.windows_service

The service supervises Frigate the way the generated launcher does. It starts
Frigate, starts it again when Frigate exited having asked for a restart, and
stops it by signalling a named event, because that is the only way a service
can ask a child process to shut down cleanly: session 0 has no console, so no
console control event can be delivered, and ``terminate()`` on Windows is
``TerminateProcess``, which would leave the recording segment being written
without a moov atom.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

IS_SERVICE = False

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    IS_SERVICE = True
except ImportError:  # pragma: no cover - a Linux checkout has no pywin32
    pass


#: how long Frigate gets to shut down before the service kills it. The SCM
#: waits about 30 seconds for a service to report itself stopped, so this has
#: to stay inside that budget.
STOP_GRACE_SECONDS = 20

#: how long a terminating Frigate gets before the service kills it outright
TERMINATE_GRACE_SECONDS = 5

#: pause between a restart Frigate asked for and starting it again, so a config
#: that makes Frigate restart immediately cannot spin
RESTART_DELAY_SECONDS = 1.0


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
        # Frigate opens this by name (FRIGATE_SERVICE_STOP_EVENT) and waits on
        # it in a thread; manual reset so every waiter stays woken, and the
        # name carries the pid so two services and leftover events from an
        # earlier run cannot be confused for each other
        self.stop_event_name = f"FrigateServiceStop-{os.getpid()}"
        self.stop_event = win32event.CreateEvent(None, 1, 0, self.stop_event_name)

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_requested = True
        # Ask Frigate to shut down the way Ctrl+C would. It finalizes the
        # segment it is writing, closes the database and takes its children
        # with it (see frigate/util/platform.py).
        win32event.SetEvent(self.stop_event)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self) -> None:
        from frigate.util.platform import SERVICE_STOP_EVENT_VAR
        from frigate.util.services import consume_restart_request

        # Session 0 has no console: SIGINT / SIGBREAK do not exist.
        # Register only the signals that can actually arrive.
        try:
            signal.signal(signal.SIGTERM, lambda s, f: self.SvcStop())
        except (ValueError, OSError):
            pass

        # Frigate's own entry point rather than the launcher: a .ps1 cannot be
        # started by Popen, and the service already runs in the venv the
        # launcher would have activated.
        env = os.environ.copy()
        env["FRIGATE_SERVICE_MODE"] = "1"
        env[SERVICE_STOP_EVENT_VAR] = self.stop_event_name

        logger = logging.getLogger("frigate_service")

        while True:
            # a stop that was requested before this start must not carry over
            win32event.ResetEvent(self.stop_event)

            process = self._start_frigate(logger, env)

            if process is None:
                break

            self._wait_for_exit(process, logger)

            if self.stop_requested:
                self._shut_down(process, logger)
                break

            if consume_restart_request():
                logger.info("Frigate asked to be restarted, starting it again")
                time.sleep(RESTART_DELAY_SECONDS)
                continue

            logger.info("Frigate exited and did not ask to be restarted")
            break

        self.ReportServiceStatus(win32service.SERVICE_STOPPED)

    def _start_frigate(
        self, logger: logging.Logger, env: dict[str, str]
    ) -> subprocess.Popen | None:
        """Start Frigate, or return None when it cannot be started at all."""
        cmd = [sys.executable, "-m", "frigate"]
        logger.info("Starting Frigate service with command: %s", cmd)

        try:
            return subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                env=env,
            )
        except OSError as err:
            logger.error("Unable to start Frigate: %s", err)
            return None

    def _wait_for_exit(self, process: subprocess.Popen, logger: logging.Logger) -> None:
        """Wait for Frigate to exit, or for the SCM to ask the service to stop.

        The wait returns as soon as a stop is requested, so the shutdown is
        handled in one place rather than in this loop.
        """
        while not self.stop_requested and process.poll() is None:
            win32event.WaitForSingleObject(self.hWaitStop, 500)

    def _shut_down(self, process: subprocess.Popen, logger: logging.Logger) -> None:
        """Let Frigate stop on its own, then make sure it is really gone."""
        if process.poll() is not None:
            return

        logger.info("Waiting up to %ss for Frigate to stop", STOP_GRACE_SECONDS)

        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
            logger.info("Frigate stopped cleanly")
            return
        except subprocess.TimeoutExpired:
            logger.warning(
                "Frigate did not stop within %ss, terminating it", STOP_GRACE_SECONDS
            )

        process.terminate()

        try:
            process.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning("Frigate did not terminate, killing it")
            process.kill()
            process.wait()


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
