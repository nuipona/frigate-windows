"""Tests for the Windows birdseye loopback transport."""

import socket
import unittest
from unittest.mock import patch

from frigate.util import birdseye_pipe
from frigate.util.birdseye_pipe import BirdseyePipe, create_birdseye_pipe

FRAME = b"\x10" * 64


class TestBirdseyePipe(unittest.TestCase):
    def setUp(self) -> None:
        # Port zero lets Windows choose a free ephemeral port for each test.
        port_patch = patch.object(birdseye_pipe, "get_birdseye_port", return_value=0)
        port_patch.start()
        self.addCleanup(port_patch.stop)
        self.pipe = BirdseyePipe()
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

    def test_write_reaches_connected_reader(self) -> None:
        reader = self.connect_reader()

        self.assertTrue(self.pipe.write(FRAME))
        self.assertTrue(self.pipe.reading)
        self.assertEqual(reader.recv(len(FRAME)), FRAME)

    def test_write_without_reader_discards_frame(self) -> None:
        self.assertFalse(self.pipe.write(FRAME))
        self.assertFalse(self.pipe.reading)

    def test_reader_disconnect_is_eventually_reported(self) -> None:
        reader = self.connect_reader()
        self.assertTrue(self.pipe.write(FRAME))
        reader.close()

        for _ in range(100):
            if not self.pipe.write(FRAME):
                break
        else:
            self.fail("the pipe never noticed its reader going away")

        self.assertFalse(self.pipe.reading)

    def test_factory_returns_windows_transport(self) -> None:
        pipe = create_birdseye_pipe()
        self.addCleanup(pipe.close)
        self.assertIsInstance(pipe, BirdseyePipe)


if __name__ == "__main__":
    unittest.main()
