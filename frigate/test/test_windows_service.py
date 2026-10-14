"""Tests for the Windows service wrapper's platform independent parts.

The SCM parts of ``frigate/util/windows_service.py`` need Windows and pywin32,
but the two decisions that are easy to get wrong are plain logic and are tested
here on every platform: which interpreter runs Frigate, and what the service
does when Frigate exits.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from frigate.util.windows_service import (
    FrigateService,
    frigate_python,
    supervision_outcome,
)


class TestFrigatePython(unittest.TestCase):
    """The service is hosted by pywin32, so sys.executable is not the app's."""

    def test_a_normal_interpreter_is_returned_as_is(self):
        self.assertEqual(frigate_python(), sys.executable)

    def test_the_service_host_uses_the_environments_interpreter(self):
        """pywin32 copies pythonservice.exe into the environment root, so the
        environment's own interpreter is next to it, in Scripts."""
        with tempfile.TemporaryDirectory() as tmp:
            host = os.path.join(tmp, "pythonservice.exe")
            interpreter = os.path.join(tmp, "Scripts", "python.exe")
            os.makedirs(os.path.dirname(interpreter))

            for path in (host, interpreter):
                open(path, "w").close()

            with patch.object(sys, "executable", host):
                self.assertEqual(frigate_python(), interpreter)

    def test_the_service_host_of_a_plain_install(self):
        """Without a virtual environment the interpreter is beside the host."""
        with tempfile.TemporaryDirectory() as tmp:
            host = os.path.join(tmp, "pythonservice.exe")
            interpreter = os.path.join(tmp, "python.exe")

            for path in (host, interpreter):
                open(path, "w").close()

            with patch.object(sys, "executable", host):
                self.assertEqual(frigate_python(), interpreter)

    def test_the_host_is_used_when_no_interpreter_is_next_to_it(self):
        """A host that may not import Frigate beats starting nothing at all."""
        with tempfile.TemporaryDirectory() as tmp:
            host = os.path.join(tmp, "pythonservice.exe")
            open(host, "w").close()

            with patch.object(sys, "executable", host):
                self.assertEqual(frigate_python(), host)


class TestSupervisionOutcome(unittest.TestCase):
    """What the service does after Frigate exits decides SCM recovery."""

    def test_a_restart_request_wins_over_the_exit_code(self):
        self.assertEqual(supervision_outcome(0, True), "restart")
        self.assertEqual(supervision_outcome(1, True), "restart")

    def test_a_clean_exit_without_a_request_stops_the_service(self):
        self.assertEqual(supervision_outcome(0, False), "stop")

    def test_a_death_fails_the_service(self):
        for exit_code in (1, 2, 137, None):
            self.assertEqual(supervision_outcome(exit_code, False), "fail")


class TestModuleSurface(unittest.TestCase):
    def test_the_class_exists_without_pywin32(self):
        """The module has to import anywhere: the Linux suite imports it too."""
        self.assertEqual(FrigateService._svc_name_, "Frigate")


if __name__ == "__main__":
    unittest.main()
