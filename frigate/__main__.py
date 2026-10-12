import argparse
import faulthandler
import logging
import multiprocessing as mp
import sys
import threading

import ruamel.yaml
from pydantic import ValidationError

from frigate.app import FrigateApp
from frigate.config import FrigateConfig
from frigate.log import setup_logging
from frigate.util.config import find_config_file
from frigate.util.platform import (
    describe,
    ensure_runtime_dirs,
    get_mp_start_method,
    install_signal_handlers,
    supports_forkserver,
)


def main() -> None:
    logger = logging.getLogger("frigate")
    manager = mp.Manager()
    faulthandler.enable()

    # Setup the logging thread
    setup_logging(manager)

    threading.current_thread().name = "frigate"
    stop_event = mp.Event()

    # Ctrl+C and, where the platform has them, SIGTERM and SIGBREAK stop the app
    install_signal_handlers(stop_event, sys.exit)

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
    if args.validate_config:
        print("*************************************************************")
        print("*** Your config file is valid.                            ***")
        print("*************************************************************")
        sys.exit(0)

    # Run the main application.
    FrigateApp(config, manager, stop_event).start()


if __name__ == "__main__":
    # Windows has no fork, so the start method has to be spawn; the preload
    # list below only applies to forkserver and is skipped there.
    if supports_forkserver():
        mp.set_forkserver_preload(
            [
                # Standard library and core dependencies
                "sqlite3",
                # Third-party libraries commonly used in Frigate
                "numpy",
                "cv2",
                "peewee",
                "zmq",
                "ruamel.yaml",
                # Frigate core modules
                "frigate.camera.maintainer",
            ]
        )

    mp.set_start_method(get_mp_start_method(), force=True)
    main()
