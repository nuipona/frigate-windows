"""Tests for the birdseye restream pipe.

Upstream writes birdseye frames to a FIFO and, when a reader that was
attached goes away, recreates that FIFO exactly once so no partially read
frame is left behind. The pipe classes here keep that contract on POSIX and
stand in for it with a loopback listener on Windows, and their ``write``
reports whether the frame reached a reader instead of recreating anything
itself: recreation is the caller's decision (see
``frigate.output.birdseye``), and doing it in both places tears the pipe
down twice for one reader going away.
"""

import os
import socket
import tempfile
import unittest
from unittest.mock import patch

from frigate.util import birdseye_pipe
from frigate.util.birdseye_pipe import (
    FifoBirdseyePipe,
    TcpBirdseyePipe,
    create_birdseye_pipe,
)

FRAME = b"\x10" * 64


@unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs only exist on POSIX")
class TestFifoBirdseyePipe(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "birdseye")
        path_patch = patch.object(
            birdseye_pipe, "birdseye_pipe_path", return_value=self.path
        )
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.pipe = FifoBirdseyePipe()
        self.addCleanup(self.pipe.close)
        self.pipe.recreate()

    def open_reader(self) -> int:
        reader = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)

        def close_reader() -> None:
            try:
                os.close(reader)
            except OSError:
                pass  # the test already closed it

        self.addCleanup(close_reader)
        return reader

    def test_write_reaches_an_attached_reader(self) -> None:
        reader = self.open_reader()

        self.assertTrue(self.pipe.write(FRAME))
        self.assertTrue(self.pipe.reading)
        self.assertEqual(os.read(reader, len(FRAME)), FRAME)

    def test_write_with_no_reader_discards_the_frame(self) -> None:
        self.assertFalse(self.pipe.write(FRAME))
        self.assertFalse(self.pipe.reading)

    def test_reader_going_away_is_reported_without_recreating(self) -> None:
        """A broken pipe returns False and leaves recreation to the caller.

        Recreating here and again in the caller would rebuild the FIFO
        twice, and a reader that reconnected after the first rebuild would
        have its FIFO removed by the second one.
        """
        reader = self.open_reader()
        self.assertTrue(self.pipe.write(FRAME))
        os.close(reader)

        with patch.object(os, "mkfifo", wraps=os.mkfifo) as mkfifo:
            self.assertFalse(self.pipe.write(FRAME))

        mkfifo.assert_not_called()
        self.assertFalse(self.pipe.reading)

    def test_recreate_replaces_the_fifo(self) -> None:
        with patch.object(os, "mkfifo", wraps=os.mkfifo) as mkfifo:
            self.pipe.recreate()

        mkfifo.assert_called_once()
        self.assertFalse(self.pipe.reading)

        # and the rebuilt FIFO carries frames to a new reader
        reader = self.open_reader()
        self.assertTrue(self.pipe.write(FRAME))
        self.assertEqual(os.read(reader, len(FRAME)), FRAME)


class TestTcpBirdseyePipe(unittest.TestCase):
    def setUp(self) -> None:
        # port 0 lets the OS pick a free one, so tests never collide
        port_patch = patch.object(birdseye_pipe, "get_birdseye_port", return_value=0)
        port_patch.start()
        self.addCleanup(port_patch.stop)
        self.pipe = TcpBirdseyePipe()
        self.addCleanup(self.pipe.close)
        self.pipe.recreate()
        self.clients: list[socket.socket] = []
        self.addCleanup(self.close_clients)

    def close_clients(self) -> None:
        for client in self.clients:
            client.close()

    def connect_reader(self) -> socket.socket:
        assert self.pipe.listener is not None
        port = self.pipe.listener.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.clients.append(client)
        return client

    def test_write_reaches_a_connected_reader(self) -> None:
        reader = self.connect_reader()

        self.assertTrue(self.pipe.write(FRAME))
        self.assertTrue(self.pipe.reading)
        self.assertEqual(reader.recv(len(FRAME)), FRAME)

    def test_write_with_no_reader_discards_the_frame(self) -> None:
        self.assertFalse(self.pipe.write(FRAME))
        self.assertFalse(self.pipe.reading)

    def test_reader_going_away_is_eventually_reported(self) -> None:
        reader = self.connect_reader()
        self.assertTrue(self.pipe.write(FRAME))
        reader.close()

        # the first write after a peer closes can still succeed before the
        # reset arrives, so keep writing until the pipe reports it, as the
        # output process does with every frame
        for _ in range(100):
            if not self.pipe.write(FRAME):
                break
        else:
            self.fail("the pipe never noticed its reader going away")

        self.assertFalse(self.pipe.reading)


class TestBirdseyePipeFactory(unittest.TestCase):
    def test_windows_uses_the_tcp_pipe(self) -> None:
        with patch.object(birdseye_pipe, "IS_WINDOWS", True):
            pipe = create_birdseye_pipe()

        self.assertIsInstance(pipe, TcpBirdseyePipe)
        pipe.close()

    def test_posix_uses_the_fifo_pipe(self) -> None:
        with patch.object(birdseye_pipe, "IS_WINDOWS", False):
            pipe = create_birdseye_pipe()

        self.assertIsInstance(pipe, FifoBirdseyePipe)
        pipe.close()


if __name__ == "__main__":
    unittest.main()
