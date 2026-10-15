# In log.py
import atexit
import io
import logging
import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from enum import Enum
from functools import wraps
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from multiprocessing.managers import SyncManager
from queue import Empty, Queue
from typing import Any, TypeVar, cast

from frigate.util.builtin import clean_camera_user_pass
from frigate.util.platform import get_service_log_path, serves_web_app

# lets a decorator keep the signature of the function it wraps
_F = TypeVar("_F", bound=Callable[..., Any])

LOG_HANDLER = logging.StreamHandler()
LOG_HANDLER.setFormatter(
    logging.Formatter(
        "[%(asctime)s] %(name)-30s %(levelname)-8s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)

# filter out norfair warning
LOG_HANDLER.addFilter(
    lambda record: (
        not record.getMessage().startswith("You are using a scalar distance function")
    )
)

# filter out tflite logging
LOG_HANDLER.addFilter(
    lambda record: (
        "Created TensorFlow Lite XNNPACK delegate for CPU." not in record.getMessage()
    )
)


class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


log_listener: QueueListener | None = None
log_queue: Queue | None = None


def service_log_handler() -> logging.Handler | None:
    """Handler writing the log to the file the web UI reads.

    Only used when Frigate is not supervised by s6, because inside the
    container s6-log already collects stdout into that file and a second
    writer would interleave with it.
    """
    if not serves_web_app():
        return None

    path = get_service_log_path("frigate")

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # bound the file: a long running install must not fill the disk
        handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=2)
        handler.setFormatter(LOG_HANDLER.formatter)
        return handler
    except OSError as err:
        logging.getLogger(__name__).warning(
            "Unable to write the log file for the web UI: %s", err
        )
        return None


def setup_logging(manager: SyncManager) -> None:
    global log_listener, log_queue
    log_queue = manager.Queue()

    handlers: list[logging.Handler] = [LOG_HANDLER]
    file_handler = service_log_handler()

    if file_handler is not None:
        handlers.append(file_handler)

    log_listener = QueueListener(log_queue, *handlers, respect_handler_level=True)

    atexit.register(_stop_logging)
    log_listener.start()

    logging.basicConfig(
        level=logging.INFO,
        handlers=[],
        force=True,
    )

    logging.getLogger().addHandler(QueueHandler(log_listener.queue))


def _stop_logging() -> None:
    global log_listener
    if log_listener is not None:
        log_listener.stop()
        log_listener = None


def apply_log_levels(default: str, log_levels: dict[str, LogLevel]) -> None:
    logging.getLogger().setLevel(default)

    log_levels = {
        "absl": LogLevel.error,
        "httpx": LogLevel.error,
        "httpx2": LogLevel.error,
        "h5py": LogLevel.error,
        "keras": LogLevel.error,
        "matplotlib": LogLevel.error,
        "tensorflow": LogLevel.error,
        "tensorflow.python": LogLevel.error,
        "werkzeug": LogLevel.error,
        "ws4py": LogLevel.error,
        "PIL": LogLevel.warning,
        "numba": LogLevel.warning,
        "google_genai.models": LogLevel.warning,
        **log_levels,
    }

    for log, level in log_levels.items():
        logging.getLogger(log).setLevel(level.value.upper())


# When a multiprocessing.Process exits, python tries to flush stdout and stderr. However, if the
# process is created after a thread (for example a logging thread) is created and the process fork
# happens while an internal lock is held, the stdout/err flush can cause a deadlock.
#
# https://github.com/python/cpython/issues/91776
def reopen_std_streams() -> None:
    sys.stdout = os.fdopen(1, "w")
    sys.stderr = os.fdopen(2, "w")


if hasattr(os, "register_at_fork"):
    # Windows has no fork, so there is nothing to reopen: children start
    # through spawn with their own streams.
    os.register_at_fork(after_in_child=reopen_std_streams)


# based on https://codereview.stackexchange.com/a/17959
class LogPipe(threading.Thread):
    def __init__(
        self, log_name: str, level: int = logging.ERROR, *, daemon: bool = False
    ):
        """Setup the object with a logger and start the thread"""
        super().__init__(daemon=daemon)
        self.logger = logging.getLogger(log_name)
        self.level = level
        self.deque: deque[str] = deque(maxlen=100)
        self.fdRead, self.fdWrite = os.pipe()
        self.pipeReader = os.fdopen(self.fdRead)
        self.start()

    def cleanup_log(self, log: str) -> str:
        """Cleanup the log line to remove sensitive info and string tokens."""
        log = clean_camera_user_pass(log).strip("\n")
        return log

    def fileno(self) -> int:
        """Return the write file descriptor of the pipe"""
        return self.fdWrite

    def run(self) -> None:
        """Run the thread, logging everything."""
        for line in iter(self.pipeReader.readline, ""):
            self.deque.append(self.cleanup_log(line))

        self.pipeReader.close()

    def dump(self) -> None:
        if not self.deque:
            return

        self.logger.log(
            self.level,
            "The following ffmpeg logs include the last 100 lines prior to exit.",
        )

        while len(self.deque) > 0:
            self.logger.log(self.level, self.deque.popleft())

    def close(self) -> None:
        """Close the write end of the pipe."""
        os.close(self.fdWrite)


class LogRedirect(io.StringIO):
    """
    A custom file-like object to capture stdout and process it.
    It extends io.StringIO to capture output and then processes it
    line by line.
    """

    def __init__(self, logger_instance: logging.Logger, level: int):
        super().__init__()
        self.logger = logger_instance
        self.log_level = level
        self._line_buffer: list[str] = []

    def write(self, s: Any) -> int:
        if not isinstance(s, str):
            s = str(s)

        self._line_buffer.append(s)

        # Process output line by line if a newline is present
        if "\n" in s:
            full_output = "".join(self._line_buffer)
            lines = full_output.splitlines(keepends=True)
            self._line_buffer = []

            for line in lines:
                if line.endswith("\n"):
                    self._process_line(line.rstrip("\n"))
                else:
                    self._line_buffer.append(line)

        return len(s)

    def _process_line(self, line: str) -> None:
        self.logger.log(self.log_level, line)

    def flush(self) -> None:
        if self._line_buffer:
            full_output = "".join(self._line_buffer)
            self._line_buffer = []
            if full_output:  # Only process if there's content
                self._process_line(full_output)

    def __enter__(self) -> "LogRedirect":
        """Context manager entry point."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit point. Ensures buffered content is flushed."""
        self.flush()


@contextmanager
def __redirect_fd_to_queue(queue: Queue[str]) -> Generator[None, None, None]:
    """Redirect file descriptor 1 (stdout) to a pipe and capture output in a queue."""
    stdout_fd = os.dup(1)
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 1)
    os.close(write_fd)

    stop_event = threading.Event()

    def reader() -> None:
        """Read from pipe and put lines in queue until stop_event is set."""
        try:
            with os.fdopen(read_fd, "r") as pipe:
                while not stop_event.is_set():
                    line = pipe.readline()
                    if not line:  # EOF
                        break
                    queue.put(line.strip())
        except OSError as e:
            queue.put(f"Reader error: {e}")
        finally:
            if not stop_event.is_set():
                stop_event.set()

    reader_thread = threading.Thread(target=reader, daemon=False)
    reader_thread.start()

    try:
        yield
    finally:
        os.dup2(stdout_fd, 1)
        os.close(stdout_fd)
        stop_event.set()
        reader_thread.join(timeout=1.0)
        try:
            os.close(read_fd)
        except OSError:
            pass


def redirect_output_to_logger(logger: logging.Logger, level: int) -> Callable[[_F], _F]:
    """Decorator to redirect both Python sys.stdout/stderr and C-level stdout to logger."""

    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            queue: Queue[str] = Queue()

            log_redirect = LogRedirect(logger, level)
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = log_redirect
            sys.stderr = log_redirect

            try:
                # Redirect C-level stdout
                with __redirect_fd_to_queue(queue):
                    result = func(*args, **kwargs)
            finally:
                # Restore Python stdout/stderr
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                log_redirect.flush()

                # Log C-level output from queue
                while True:
                    try:
                        logger.log(level, queue.get_nowait())
                    except Empty:
                        break

            return result

        return cast(_F, wrapper)

    return decorator


def suppress_os_output(func: Callable) -> Callable:
    """Discard C-level stdout and stderr while ``func`` runs.

    Redirecting to an unread pipe can block once its buffer fills. ``os.devnull``
    is available on both Windows and POSIX and safely discards arbitrarily large
    native-library output.
    """

    @wraps(func)
    def wrapper(*args: tuple, **kwargs: dict[str, Any]) -> Any:
        with ExitStack() as cleanup:
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            cleanup.callback(os.close, devnull_fd)

            original_stdout_fd = os.dup(1)
            cleanup.callback(os.close, original_stdout_fd)
            original_stderr_fd = os.dup(2)
            cleanup.callback(os.close, original_stderr_fd)

            os.dup2(devnull_fd, 1)
            cleanup.callback(os.dup2, original_stdout_fd, 1)
            os.dup2(devnull_fd, 2)
            cleanup.callback(os.dup2, original_stderr_fd, 2)

            return func(*args, **kwargs)

    return wrapper


@contextmanager
def suppress_stderr_during(operation_name: str) -> Generator[None, None, None]:
    """
    Context manager to suppress stderr output during a specific operation.

    Useful for silencing LLVM debug output, CUDA messages, and other native
    library logging that cannot be controlled via Python logging or environment
    variables. Completely redirects file descriptor 2 (stderr) to /dev/null.

    Usage:
        with suppress_stderr_during("model_conversion"):
            converter = tf.lite.TFLiteConverter.from_keras_model(model)
            tflite_model = converter.convert()

    Args:
        operation_name: Name of the operation for debugging purposes
    """
    original_stderr_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(original_stderr_fd, 2)
        os.close(devnull)
        os.close(original_stderr_fd)
