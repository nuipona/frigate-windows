"""Tests for native Windows networking config validation."""

import unittest

from pydantic import ValidationError

from frigate.config.network import ListenConfig


class TestListenConfig(unittest.TestCase):
    def test_defaults_are_distinct(self):
        listen = ListenConfig()

        self.assertEqual(listen.internal_port, 5000)
        self.assertEqual(listen.external_port, 8971)

    def test_numeric_strings_are_parsed(self):
        listen = ListenConfig(internal="5001", external="8971")

        self.assertEqual(listen.internal_port, 5001)
        self.assertEqual(listen.external_port, 8971)

    def test_identical_ports_rejected(self):
        with self.assertRaises(ValidationError):
            ListenConfig(internal=8971, external=8971)

    def test_ports_must_be_in_tcp_range(self):
        with self.assertRaises(ValidationError):
            ListenConfig(internal=0)
        with self.assertRaises(ValidationError):
            ListenConfig(external=65536)

    def test_distinct_ports_accepted(self):
        listen = ListenConfig(internal=5001, external=8971)

        self.assertEqual(listen.internal_port, 5001)
        self.assertEqual(listen.external_port, 8971)


if __name__ == "__main__":
    unittest.main(verbosity=2)
