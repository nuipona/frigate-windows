"""Loopback TCP transport for raw birdseye restream frames on Windows."""

import logging
import socket

from frigate.util.platform import birdseye_pipe_source, get_birdseye_port

logger = logging.getLogger(__name__)
WRITE_TIMEOUT = 5.0


class BirdseyePipe:
    """A single-reader loopback sink for raw birdseye frames."""

    def __init__(self) -> None:
        self.reading = False
        self.port = get_birdseye_port()
        self.listener: socket.socket | None = None
        self.connection: socket.socket | None = None

    @property
    def source(self) -> str:
        return birdseye_pipe_source()

    def recreate(self) -> None:
        self._drop_connection()
        self._drop_listener()

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", self.port))
            listener.listen(1)
            listener.setblocking(False)
        except OSError as err:
            logger.warning(
                "Unable to listen on 127.0.0.1:%s for birdseye restreaming: %s",
                self.port,
                err,
            )
            listener.close()
            return

        self.listener = listener
        self.reading = False

    def _accept(self) -> None:
        if self.listener is None:
            return
        try:
            connection, _ = self.listener.accept()
        except BlockingIOError:
            return
        except OSError as err:
            logger.debug("Birdseye connection accept failed: %s", err)
            return

        connection.settimeout(WRITE_TIMEOUT)
        self.connection = connection

    def write(self, data: bytes) -> bool:
        if self.listener is None:
            self.recreate()
        if self.listener is None:
            return False
        if self.connection is None:
            self._accept()
        if self.connection is None:
            self.reading = False
            return False

        try:
            self.connection.sendall(data)
        except (TimeoutError, OSError) as err:
            logger.debug("Birdseye reader disconnected: %s", err)
            self._drop_connection()
            self.reading = False
            return False

        self.reading = True
        return True

    def _drop_connection(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None

    def _drop_listener(self) -> None:
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
            self.listener = None

    def close(self) -> None:
        self._drop_connection()
        self._drop_listener()
        self.reading = False


def create_birdseye_pipe() -> BirdseyePipe:
    return BirdseyePipe()
