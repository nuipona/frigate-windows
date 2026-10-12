"""The pipe birdseye frames are restreamed through.

When ``birdseye.restream`` is enabled the birdseye output process writes raw
frames to a pipe and go2rtc runs an ffmpeg process that reads them and publishes
an RTSP stream. In the container that pipe is a FIFO created with ``os.mkfifo``.

Windows has no FIFOs, so a loopback TCP listener stands in for one. go2rtc's
ffmpeg reads the identical rawvideo stream from ``tcp://127.0.0.1:<port>`` and
every other part of the pipeline (the writer, the reader-is-gone detection, the
frame drop on a stalled reader) behaves the same way.

The FIFO semantics that are preserved:

- frames written with no reader attached are discarded rather than buffered
  forever
- a reader that goes away is detected on the next write, which lets the output
  process recreate the pipe so no partially read frame is left behind
- at most one writer and one reader
"""

import logging
import os
import socket

from frigate.util.platform import (
    IS_WINDOWS,
    birdseye_pipe_path,
    birdseye_pipe_source,
    get_birdseye_port,
)

logger = logging.getLogger(__name__)

#: how long a write may stall before the reader is considered gone
WRITE_TIMEOUT = 5.0


class BirdseyePipe:
    """A write-only sink for raw birdseye frames."""

    def __init__(self) -> None:
        self.reading = False

    @property
    def source(self) -> str:
        """The input go2rtc's ffmpeg reads frames from."""
        return birdseye_pipe_source()

    def recreate(self) -> None:
        """(Re)create the underlying handle, discarding any reader."""
        raise NotImplementedError

    def write(self, data: bytes) -> bool:
        """Write a frame, returning whether it reached a reader.

        A ``False`` return means the reader is gone and the caller should
        recreate the pipe before writing again.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release the underlying handle."""
        raise NotImplementedError


class FifoBirdseyePipe(BirdseyePipe):
    """The POSIX implementation, backed by a named pipe."""

    def __init__(self) -> None:
        super().__init__()
        self.path = birdseye_pipe_path()
        self.fd: int | None = None

    def recreate(self) -> None:
        self.close()

        if os.path.exists(self.path):
            os.remove(self.path)

        os.mkfifo(self.path, mode=0o777)

        # opening for read and then for write is what keeps the writer from
        # blocking on an open until a reader appears
        reader = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)

        try:
            self.fd = os.open(self.path, os.O_WRONLY)
        finally:
            os.close(reader)

        self.reading = False

    def write(self, data: bytes) -> bool:
        if self.fd is None:
            return False

        try:
            os.write(self.fd, data)
        except BrokenPipeError:
            if self.reading:
                # a reader was attached and stopped reading part way through a
                # frame, so the pipe has to be recreated to drop the partial
                # frame rather than let it corrupt the stream
                logger.debug("Birdseye pipe reader went away, recreating the pipe")
                self.recreate()

            self.reading = False
            return False

        self.reading = True
        return True

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass

            self.fd = None

        self.reading = False


class TcpBirdseyePipe(BirdseyePipe):
    """The Windows implementation, backed by a loopback listener."""

    def __init__(self) -> None:
        super().__init__()
        self.port = get_birdseye_port()
        self.listener: socket.socket | None = None
        self.connection: socket.socket | None = None

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
            self.listener = None
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
            logger.debug("Birdseye pipe accept failed: %s", err)
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
            # no reader yet, so there is nobody to send this frame to
            self.reading = False
            return False

        try:
            self.connection.sendall(data)
        except (TimeoutError, OSError) as err:
            logger.debug("Birdseye pipe reader went away: %s", err)
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
    """The pipe implementation for this platform."""
    if IS_WINDOWS:
        return TcpBirdseyePipe()

    return FifoBirdseyePipe()
