"""Windows SCM service wrapper for Frigate, using pywin32.

pywin32 is part of docker/main/requirements-windows.txt, so a native install
already has the SCM modules this imports.

Install: powershell -ExecutionPolicy Bypass -File scripts/install_frigate_service.ps1 -Action Install
Debug:   python -m frigate.util.windows_service debug
Remove:  python -m frigate.util.windows_service remove

pywin32 registers the service with its own ``pythonservice.exe`` host and
records this class in the registry, so the SCM starts a process that imports
``FrigateService`` and calls ``SvcRun()``. That host reports the service's
status: it reports RUNNING before ``SvcDoRun`` runs and STOPPED after it
returns, with an error code when it raised. ``SvcDoRun`` therefore never
reports STOPPED itself, and raises when Frigate died.

The service supervises Frigate the way the generated launcher does. It starts
Frigate, starts it again when Frigate exited having asked for a restart, and
stops it by setting a named event. A named event is the only channel that
reaches a child in session 0, and it lets Frigate finalize the recording
segment it is writing instead of being terminated.

Two things are easy to get wrong in a service, so this module is explicit about
both:

* the environment. A service does not inherit the installing user's
  environment, so ``LOCALAPPDATA`` belongs to the service account and Frigate
  would keep the config and the recordings in the service profile. The install
  script pins ``FRIGATE_BASE_DIR`` in the service's own environment; when that
  is missing this logs a warning rather than quietly recording somewhere the
  user will not find it.
* failing. Frigate dying used to look like a clean stop, so the SCM never
  applied its recovery actions. A death now raises, which pywin32 turns into a
  failed status: the event log gets the reason and Windows restarts the service
  (the installer configures that, since the SCM does nothing by default).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

from frigate.util.platform import (
    BASE_DIR_VAR,
    SERVICE_STOP_EVENT_VAR,
    get_base_dir,
)

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    IS_SERVICE = True
except ImportError:  # pragma: no cover - a Linux checkout has no pywin32
    IS_SERVICE = False


#: how long Frigate gets to shut down before the service kills it. The SCM
#: waits about 30 seconds for a service to report itself stopped, so this has
#: to stay inside that budget.
STOP_GRACE_SECONDS = 20

#: how long a terminating Frigate gets before the service kills it outright
TERMINATE_GRACE_SECONDS = 5

#: what the SCM is told a stop can take, in milliseconds: the shutdown grace
#: plus the terminating grace plus a moment for the bookkeeping. pywin32
#: defaults to 5s, which is less than a graceful stop can take.
STOP_WAIT_HINT_MS = (STOP_GRACE_SECONDS + TERMINATE_GRACE_SECONDS + 5) * 1000

#: pause between a restart Frigate asked for and starting it again, so a config
#: that makes Frigate restart immediately cannot spin
RESTART_DELAY_SECONDS = 1.0


class FrigateServiceFailure(RuntimeError):
    """Frigate stopped when the service expected it to keep running.

    Raised out of ``SvcDoRun``. pywin32 catches it and tells the SCM the
    service stopped with an error, which is what the SCM's recovery actions key
    off and what puts the reason in the event log.
    """


def frigate_python() -> str:
    """The interpreter that runs Frigate.

    The SCM starts pywin32's ``pythonservice.exe``, which hosts ``FrigateService``,
    so inside the service ``sys.executable`` is that host and not the
    interpreter that owns the environment Frigate is installed in. The host is
    a copy of the interpreter's executable, so the real one is next to it: in a
    virtual environment (the host lands in the environment root) it is in
    ``Scripts``, and in a plain install it is beside the host.
    """
    executable = sys.executable

    if "pythonservice" not in os.path.basename(executable).lower():
        return executable

    root = os.path.dirname(executable)

    for candidate in (
        os.path.join(root, "python.exe"),
        os.path.join(root, "Scripts", "python.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate

    return executable


def supervision_outcome(exit_code: int | None, restart_requested: bool) -> str:
    """What the service should do after Frigate exited on its own.

    ``restart``: Frigate asked to be started again, which is how the UI's
    restart action and the detector watchdog work. ``stop``: Frigate exited
    cleanly without asking for anything, so it stays stopped. ``fail``: Frigate
    died, so the service reports a failure instead of a stop.
    """
    if restart_requested:
        return "restart"

    if exit_code == 0:
        return "stop"

    return "fail"


def log_base_dir(logger: logging.Logger) -> str | None:
    """Log where Frigate will keep its data, and object to a profile default.

    A service does not get the installing user's environment, so without an
    explicit ``FRIGATE_BASE_DIR`` the base directory resolves from the service
    account's profile. The install script writes that variable into the
    service's own environment for exactly this reason; when it is missing, say
    so instead of silently using the profile.
    """
    try:
        base_dir = get_base_dir()
    except Exception as err:  # pragma: no cover - the app has to be importable
        logger.warning("Unable to resolve the Frigate base directory: %s", err)
        return None

    logger.info("Frigate's base directory is %s", base_dir)

    if not os.environ.get(BASE_DIR_VAR):
        message = (
            f"No {BASE_DIR_VAR} is set for the service, so Frigate will use the "
            f"service account's own directory rather than the install's: {base_dir}. "
            "Run scripts/install_frigate_service.ps1 -Action Configure to pin it."
        )
        logger.warning("%s", message)
        _log_warning_to_event_log(message)

    return base_dir


def _log_warning_to_event_log(message: str) -> None:
    """Also put the message where a service is read: the event log."""
    try:
        servicemanager.LogWarningMsg(message)
    except Exception:  # pragma: no cover - needs the SCM and pywin32
        pass


# pywin32's ServiceFramework, or a plain base class off Windows. The class is
# defined either way so what does not need the SCM - the interpreter lookup
# and the exit policy above - stays importable and testable on any platform,
# and importing this module never fails because pywin32 is missing.
class FrigateService(win32serviceutil.ServiceFramework if IS_SERVICE else object):
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
        # Frigate opens this by name (FRIGATE_SERVICE_STOP_EVENT) and waits
        # on it in a thread; manual reset so every waiter stays woken, and
        # the name carries the pid so two services and leftover events from
        # an earlier run cannot be confused for each other
        self.stop_event_name = f"FrigateServiceStop-{os.getpid()}"
        self.stop_event = win32event.CreateEvent(None, 1, 0, self.stop_event_name)

    def SvcStop(self) -> None:
        self.ReportServiceStatus(
            win32service.SERVICE_STOP_PENDING, waitHint=STOP_WAIT_HINT_MS
        )
        self.stop_requested = True
        # Ask Frigate to shut down the way Ctrl+C would. It finalizes the
        # segment it is writing, closes the database and takes its children
        # with it (see frigate/util/platform.py).
        win32event.SetEvent(self.stop_event)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self) -> None:
        from frigate.util.services import consume_restart_request

        logger = logging.getLogger("frigate_service")

        log_base_dir(logger)

        # Session 0 has no console: SIGINT / SIGBREAK do not exist.
        # Register only the signals that can actually arrive.
        try:
            signal.signal(signal.SIGTERM, lambda s, f: self.SvcStop())
        except (ValueError, OSError):
            pass

        # Frigate's own entry point rather than the launcher: a .ps1 cannot
        # be started by Popen, and the service already runs in the
        # environment the launcher would have activated.
        env = os.environ.copy()
        env["FRIGATE_SERVICE_MODE"] = "1"
        env[SERVICE_STOP_EVENT_VAR] = self.stop_event_name

        while True:
            # a stop that was requested before this start must not carry over
            win32event.ResetEvent(self.stop_event)

            process = self._start_frigate(logger, env)

            if process is None:
                raise FrigateServiceFailure("Frigate could not be started")

            self._wait_for_exit(process, logger)

            if self.stop_requested:
                self._shut_down(process, logger)
                # The SCM asked for this stop, so it is a clean one: return
                # normally and the host reports the service as stopped.
                logger.info("Frigate stopped on request")
                return

            outcome = supervision_outcome(process.returncode, consume_restart_request())

            if outcome == "restart":
                logger.info("Frigate asked to be restarted, starting it again")
                time.sleep(RESTART_DELAY_SECONDS)
                continue

            if outcome == "fail":
                raise FrigateServiceFailure(
                    f"Frigate exited with code {process.returncode} and did not "
                    "ask to be restarted"
                )

            logger.info("Frigate exited and did not ask to be restarted")
            return

    def _start_frigate(
        self, logger: logging.Logger, env: dict[str, str]
    ) -> subprocess.Popen | None:
        """Start Frigate, or return None when it cannot be started at all."""
        cmd = [frigate_python(), "-m", "frigate"]
        logger.info("Starting Frigate with %s", " ".join(cmd))

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
                "Frigate did not stop within %ss, terminating it",
                STOP_GRACE_SECONDS,
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
        print("pywin32 is not installed, so the SCM service cannot be used.")
        print("It is part of the native Windows requirements:")
        print("    pip install -r docker/main/requirements-windows.txt")
        print("Then install the service with:")
        print("    scripts/install_frigate_service.ps1 -Action Install")
        sys.exit(1)

    # install, update, remove, start, stop, restart and debug, with --startup,
    # --username, --password, --interactive and --wait. The installer uses this
    # rather than calling pywin32's install API, so the service's name,
    # display name and description live here and nowhere else.
    win32serviceutil.HandleCommandLine(FrigateService)
