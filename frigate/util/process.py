import faulthandler
import logging
import multiprocessing as mp
import threading
from collections.abc import Callable
from logging.handlers import QueueHandler
from multiprocessing.synchronize import Event as MpEvent

import frigate.log
from frigate.config.logger import LoggerConfig
from frigate.util.platform import set_process_priority


class BaseProcess(mp.Process):
    def __init__(
        self,
        stop_event: MpEvent,
        priority: int,
        *,
        name: str | None = None,
        target: Callable | None = None,
        args: tuple = (),
        kwargs: dict = {},
        daemon: bool | None = None,
    ):
        self.priority = priority
        self.stop_event = stop_event
        super().__init__(
            name=name, target=target, args=args, kwargs=kwargs, daemon=daemon
        )

    def start(self, *args, **kwargs):
        self.before_start()

        try:
            super().start(*args, **kwargs)
        except AssertionError:
            # Windows has no fork, so a daemonic process may not create another
            # process. Camera processes are started from a worker thread, and
            # the maintainer itself is started from a process on some code
            # paths, so the daemon flag has to give way rather than lose the
            # worker. Shutdown is still driven by the stop event and the
            # watchdog, which is what actually reaps workers.
            self.daemon = False
            super().start(*args, **kwargs)

        self.after_start()

    def before_start(self) -> None:
        pass

    def after_start(self) -> None:
        pass


class FrigateProcess(BaseProcess):
    logger: logging.Logger

    def before_start(self) -> None:
        self.__log_queue = frigate.log.log_listener.queue

    def pre_run_setup(self, logConfig: LoggerConfig | None = None) -> None:
        set_process_priority(self.priority)

        threading.current_thread().name = f"process:{self.name}"
        faulthandler.enable()

        # setup logging
        self.logger = logging.getLogger(self.name)
        logging.basicConfig(handlers=[], force=True)
        logging.getLogger().addHandler(QueueHandler(self.__log_queue))

        # Always apply base log level suppressions for noisy third-party libraries
        # even if no specific logConfig is provided
        if logConfig:
            frigate.log.apply_log_levels(
                logConfig.default.value.upper(), logConfig.logs
            )
        else:
            # Apply default INFO level with standard library suppressions
            frigate.log.apply_log_levels("INFO", {})
