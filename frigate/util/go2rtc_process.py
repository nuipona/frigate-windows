"""Run go2rtc as a managed Windows child process.

Frigate starts the installed executable, writes its output to the native log
directory, and terminates it during shutdown. Live view needs go2rtc for MSE,
WebRTC, and camera restreaming; a missing executable is reported as a warning so
the rest of Frigate can still start.
"""

import logging
import os
import subprocess as sp

from frigate.util.go2rtc_config import prepare_homekit_config
from frigate.util.platform import (
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
    """Locate ``go2rtc.exe`` in the native install or on ``PATH``."""
    from frigate.util.platform import executable_name, get_config_dir

    roots = [config_dir or get_config_dir(), get_install_dir()]

    for root in roots:
        for location in BINARY_LOCATIONS:
            candidate = os.path.join(root, location)

            if candidate.endswith("go2rtc"):
                candidate = executable_name(candidate)

            if not os.path.isfile(candidate):
                continue

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

        command = [binary]
        try:
            homekit_config = prepare_homekit_config()
        except (OSError, ValueError) as err:
            logger.warning(
                "HomeKit pairing state will not persist because its config "
                "could not be prepared: %s",
                err,
            )
        else:
            # go2rtc writes HomeKit pairings to the first config file.
            command.extend(["-config", homekit_config])
        command.extend(["-config", self.config_path])

        kwargs: dict = {
            "stdout": self.log_file or sp.DEVNULL,
            "stderr": sp.STDOUT,
            "stdin": sp.DEVNULL,
        }

        # Keep go2rtc out of the console's Ctrl+C group so Frigate can stop it
        # after its own shutdown work is done.
        kwargs["creationflags"] = sp.CREATE_NEW_PROCESS_GROUP

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
