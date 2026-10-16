import argparse
import faulthandler
import logging
import multiprocessing as mp
import sys
import threading
from multiprocessing.managers import SyncManager

import ruamel.yaml
from pydantic import ValidationError

from frigate.app import FrigateApp
from frigate.config import FrigateConfig
from frigate.log import setup_logging
from frigate.util.config import find_config_file
from frigate.util.job_object import install as install_job_object
from frigate.util.platform import (
    describe,
    ensure_runtime_dirs,
    get_mp_start_method,
    install_service_stop_watcher,
    install_signal_handlers,
    long_path_warning,
    require_windows,
)


def _create_process_manager() -> SyncManager:
    """Install Windows process containment before spawning the manager."""
    # The manager is the first child Frigate creates. Put the main process in
    # its job object first so the manager and every later child inherit it.
    install_job_object()

    return mp.Manager()


def main() -> None:
    # This is a native Windows application. Reject other operating systems
    # before creating child processes, directories, loggers, or services.
    require_windows()

    logger = logging.getLogger("frigate")
    manager = _create_process_manager()
    faulthandler.enable()

    # Setup the logging thread
    setup_logging(manager)

    threading.current_thread().name = "frigate"
    stop_event = mp.Event()

    # Ctrl+C and, where the platform has them, SIGTERM and SIGBREAK stop the app
    install_signal_handlers(stop_event, sys.exit)

    # A Windows service cannot deliver a console signal to this process, so it
    # asks through a named event instead (see frigate/util/platform.py)
    install_service_stop_watcher(stop_event)

    # Create the config, media, cache and log directories a native install needs
    ensure_runtime_dirs()
    logger.info("Starting Frigate on %s", describe())

    # Parse the cli arguments.
    parser = argparse.ArgumentParser(
        prog="Frigate",
        description="An NVR with realtime local object detection for IP cameras.",
    )
    parser.add_argument("--validate-config", action="store_true")
    args = parser.parse_args()

    # Load the configuration.
    try:
        config = FrigateConfig.load(install=True)
    except ValidationError as e:
        print("*************************************************************")
        print("*************************************************************")
        print("***    Your config file is not valid!                     ***")
        print("***    Please check the docs at                           ***")
        print("***    https://docs.frigate.video/configuration/          ***")
        print("*************************************************************")
        print("*************************************************************")
        print("***    Config Validation Errors                           ***")
        print("*************************************************************\n")
        # Attempt to get the original config file for line number tracking
        config_path = find_config_file()
        with open(config_path, encoding="utf-8") as f:
            yaml_config = ruamel.yaml.YAML()
            yaml_config.preserve_quotes = True
            full_config = yaml_config.load(f)

        for error in e.errors():
            error_path = error["loc"]

            current = full_config
            line_number = "Unknown"
            last_line_number = "Unknown"

            try:
                for i, part in enumerate(error_path):
                    key: int | str = (
                        int(part) if isinstance(part, str) and part.isdigit() else part
                    )

                    if isinstance(current, ruamel.yaml.comments.CommentedMap):
                        current = current[key]
                    elif isinstance(current, list):
                        if isinstance(key, int):
                            current = current[key]

                    if hasattr(current, "lc"):
                        last_line_number = current.lc.line

                    if i == len(error_path) - 1:
                        if hasattr(current, "lc"):
                            line_number = current.lc.line
                        else:
                            line_number = last_line_number

            except Exception as traverse_error:
                print(f"Could not determine exact line number: {traverse_error}")

            if current != full_config:
                print(f"Line #  : {line_number}")
                print(f"Key     : {' -> '.join(map(str, error_path))}")
                print(f"Value   : {error.get('input', '-')}")
            print(f"Message : {error.get('msg', error.get('type', 'Unknown'))}\n")

        print("*************************************************************")
        print("***    End Config Validation Errors                       ***")
        print("*************************************************************")

        # force a non-zero exit code for config failures
        if args.validate_config:
            sys.exit(1)

        # attempt to start Frigate in recovery mode
        try:
            config = FrigateConfig.load(install=True, safe_load=True)
            print("Starting Frigate in safe mode.")
        except ValidationError:
            print("Unable to start Frigate in safe mode.")
            sys.exit(1)
    # A path that approaches MAX_PATH fails when a segment is written, long
    # after startup succeeded, so say so now while it can still be changed.
    path_warning = long_path_warning(config.cameras.keys())

    if path_warning:
        logger.warning("%s", path_warning)

    if args.validate_config:
        print("*************************************************************")
        print("*** Your config file is valid.                            ***")
        print("*************************************************************")
        sys.exit(0)

    # Run the main application.
    FrigateApp(config, manager, stop_event).start()


if __name__ == "__main__":
    mp.set_start_method(get_mp_start_method(), force=True)
    main()
