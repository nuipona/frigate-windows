"""Tests for closing the config pub/sub sockets.

A publisher or subscriber that is dropped without ``stop()`` used to leave
its zmq context to the garbage collector, which terminates the context with
the socket still open and blocks the whole process on it. Several callers
build a subscriber just to read the current config (the storage maintainer
is one) and never run the part of the lifecycle that stops it, so dropping
one has to be safe on its own.
"""

import gc
import os
import unittest

from frigate.comms.config_updater import (
    SOCKET_PUB_SUB,
    ConfigPublisher,
    ConfigSubscriber,
)


def _ensure_bindable() -> None:
    """A publisher binds a unix socket, whose directory has to exist."""
    if SOCKET_PUB_SUB.startswith("ipc://"):
        os.makedirs(os.path.dirname(SOCKET_PUB_SUB.removeprefix("ipc://")), exist_ok=True)


class TestConfigSocketCleanup(unittest.TestCase):
    def test_subscriber_stop_is_idempotent(self):
        subscriber = ConfigSubscriber("config/cameras")

        subscriber.stop()
        # collection runs stop again, and a second close must not raise
        subscriber.stop()

        self.assertTrue(subscriber.socket.closed)
        self.assertTrue(subscriber.context.closed)

    def test_dropping_a_subscriber_closes_it(self):
        subscriber = ConfigSubscriber("config/cameras")
        socket = subscriber.socket
        context = subscriber.context

        del subscriber
        gc.collect()

        self.assertTrue(socket.closed)
        self.assertTrue(context.closed)

    def test_publisher_stop_is_idempotent(self):
        _ensure_bindable()
        publisher = ConfigPublisher()

        publisher.stop()
        publisher.stop()

        self.assertTrue(publisher.socket.closed)
        self.assertTrue(publisher.context.closed)

    def test_dropping_a_publisher_closes_it(self):
        _ensure_bindable()
        publisher = ConfigPublisher()
        socket = publisher.socket
        context = publisher.context

        del publisher
        gc.collect()

        self.assertTrue(socket.closed)
        self.assertTrue(context.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
