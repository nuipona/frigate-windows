"""Run go2rtc as a child process of a native Frigate install.

The container image starts go2rtc from an s6 service and lets s6 restart it if
it dies. A native install has no supervisor, so Frigate starts the binary it
found, keeps its output in the log directory the web UI reads, and stops it on
the way out.

Live view needs go2rtc: MSE and WebRTC are served by it, and it is the only
thing that can restream a camera without recording it. Frigate still runs
without it, so a missing binary is a warning rather than a failure.
"""

import logging
import os
import platform
import subprocess as sp

from frigate.util.platform import (
    IS_WINDOWS,
    find_executable,
    get_install_dir,
    get_service_log_path,
)

logger = logging.getLogger(__name__)

#: where a go2rtc binary may be dropped, in preference order
BINARY_LOCATIONS = (
    "go2rtc",
    os.path.join("go2rtc", "bin", "go2rtc"),
    os.path.join("bin", "go2rtc"),
)


def find_go2rtc(config_dir: str | None = None) -> str | None:
    """Locate a go2rtc binary.

    Looks next to the config file first, so a user can drop a build in the same
    place as ``config.yml`` the way the container allows, then on ``PATH``.
    """
    from frigate.util.platform import executable_name, get_config_dir

    roots = [config_dir or get_config_dir(), get_install_dir()]

    for root in roots:
        for location in BINARY_LOCATIONS:
            candidate = os.path.join(root, location)

            if candidate.endswith("go2rtc"):
                candidate = executable_name(candidate)

            if not os.path.isfile(candidate):
                continue

            # os.access with X_OK is derived from the file extension on
            # Windows, so a go2rtc.exe is executable by definition there and
            # only the POSIX permission bit is worth checking
            if IS_WINDOWS or os.access(candidate, os.X_OK):
                return candidate

    return find_executable("go2rtc")


class Go2rtcProcess:
    """The go2rtc process, when Frigate has to run it itself."""

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.process: sp.Popen | None = None
        self.log_file = None

    def start(self) -> bool:
        """Start go2rtc, returning whether it was started."""
        binary = find_go2rtc()

        if binary is None:
            logger.warning(
                "go2rtc was not found. Live view (MSE/WebRTC) will not work "
                "until a go2rtc binary is installed next to config.yml or on "
                "PATH."
            )
            return False

        if not os.path.isfile(self.config_path):
            logger.warning(
                "No go2rtc config at %s, so go2rtc has nothing to serve.",
                self.config_path,
            )

        log_path = get_service_log_path("go2rtc")

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            self.log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        except OSError as err:
            logger.warning("Unable to open %s for go2rtc's output: %s", log_path, err)
            self.log_file = None

        command = [binary, "-config", self.config_path]
        kwargs: dict = {
            "stdout": self.log_file or sp.DEVNULL,
            "stderr": sp.STDOUT,
            "stdin": sp.DEVNULL,
        }

        if platform.system() == "Windows":
            # keep go2rtc out of the console's Ctrl+C group so Frigate can stop
            # it after its own shutdown work is done
            kwargs["creationflags"] = sp.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        try:
            self.process = sp.Popen(command, **kwargs)
        except OSError as err:
            logger.error("Unable to start go2rtc: %s", err)
            return False

        logger.info("Started go2rtc: %s", self.process.pid)
        return True

    def stop(self) -> None:
        """Stop go2rtc, if it is running."""
        if self.process is not None and self.process.poll() is None:
            logger.info("Stopping go2rtc: %s", self.process.pid)
            self.process.terminate()

            try:
                self.process.wait(timeout=10)
            except sp.TimeoutExpired:
                logger.warning("go2rtc did not exit, killing it")
                self.process.kill()
                self.process.wait()

        self.process = None

        if self.log_file is not None:
            try:
                self.log_file.close()
            except OSError:
                pass

            self.log_file = None
