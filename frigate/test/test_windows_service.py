"""Tests for the Windows service wrapper.

The SCM itself needs Windows and pywin32, but the wrapper's interpreter,
supervision and stop/restart decisions can be exercised with mocked service
handles on every platform.
"""

import os
import subprocess as sp
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

import frigate.util.windows_service as windows_service
from frigate.util.windows_service import (
    FrigateService,
    FrigateServiceFailure,
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


class TestFrigateServiceStart(unittest.TestCase):
    def test_child_starts_in_source_root(self):
        """The SCM's System32 cwd must not hide database migrations."""
        service = FrigateService.__new__(FrigateService)
        process = Mock()
        env = {"FRIGATE_SERVICE_MODE": "1"}
        source_root = os.path.join("C:\\", "Frigate")

        with (
            patch.object(windows_service, "frigate_python", return_value="python.exe"),
            patch.object(windows_service, "get_source_root", return_value=source_root),
            patch.object(
                windows_service.subprocess, "Popen", return_value=process
            ) as popen,
        ):
            started = service._start_frigate(Mock(), env)

        self.assertIs(started, process)
        popen.assert_called_once_with(
            ["python.exe", "-m", "frigate"],
            cwd=source_root,
            stdin=sp.DEVNULL,
            stdout=None,
            stderr=None,
            env=env,
        )

    def test_start_failure_is_reported(self):
        service = FrigateService.__new__(FrigateService)
        logger = Mock()

        with patch.object(
            windows_service.subprocess,
            "Popen",
            side_effect=OSError("cannot start"),
        ):
            self.assertIsNone(service._start_frigate(logger, {}))

        logger.error.assert_called_once()


class TestFrigateServiceStopLifecycle(unittest.TestCase):
    def _service(self) -> FrigateService:
        service = FrigateService.__new__(FrigateService)
        service.hWaitStop = object()
        service.stop_event = object()
        service.stop_event_name = "FrigateServiceStop-test"
        service.stop_requested = False
        service.ReportServiceStatus = Mock()
        return service

    def test_stop_before_service_run_does_not_start_a_child(self):
        """A stop sent before SvcDoRun must not start then kill Frigate."""
        service = self._service()
        service._start_frigate = Mock()
        service._shut_down = Mock()

        win32event = Mock()
        win32service = SimpleNamespace(SERVICE_STOP_PENDING=0x00000102)

        with (
            patch.object(windows_service, "win32event", win32event, create=True),
            patch.object(windows_service, "win32service", win32service, create=True),
            patch.object(windows_service, "log_base_dir"),
        ):
            service.SvcStop()
            service.SvcDoRun()

        win32event.SetEvent.assert_has_calls(
            [call(service.stop_event), call(service.hWaitStop)]
        )
        win32event.ResetEvent.assert_not_called()
        service._start_frigate.assert_not_called()
        service._shut_down.assert_not_called()

    def test_stop_during_restart_delay_prevents_a_second_child(self):
        service = self._service()
        service.stop_requested = False
        process = Mock(returncode=0)
        service._start_frigate = Mock(return_value=process)
        service._wait_for_exit = Mock()
        service._shut_down = Mock()

        win32event = Mock()
        win32service = SimpleNamespace(SERVICE_STOP_PENDING=0x00000102)
        services_module = ModuleType("frigate.util.services")
        services_module.consume_restart_request = Mock(return_value=True)

        with (
            patch.dict(sys.modules, {"frigate.util.services": services_module}),
            patch.object(windows_service, "win32event", win32event, create=True),
            patch.object(windows_service, "win32service", win32service, create=True),
            patch.object(windows_service, "log_base_dir"),
            patch.object(
                windows_service.time,
                "sleep",
                side_effect=lambda _delay: service.SvcStop(),
            ),
        ):
            service.SvcDoRun()

        service._start_frigate.assert_called_once()
        service._wait_for_exit.assert_called_once_with(process, ANY)
        service._shut_down.assert_not_called()
        services_module.consume_restart_request.assert_called_once()
        win32event.SetEvent.assert_has_calls(
            [call(service.stop_event), call(service.hWaitStop)]
        )

    def test_stop_consumes_a_restart_marker_after_child_shutdown(self):
        service = self._service()
        process = Mock(returncode=0)
        service._start_frigate = Mock(return_value=process)
        service._wait_for_exit = Mock(
            side_effect=lambda _process, _logger: setattr(
                service, "stop_requested", True
            )
        )
        service._shut_down = Mock()

        win32event = Mock()
        win32service = SimpleNamespace(SERVICE_STOP_PENDING=0x00000102)
        services_module = ModuleType("frigate.util.services")
        services_module.consume_restart_request = Mock(return_value=True)

        with (
            patch.dict(sys.modules, {"frigate.util.services": services_module}),
            patch.object(windows_service, "win32event", win32event, create=True),
            patch.object(windows_service, "win32service", win32service, create=True),
            patch.object(windows_service, "log_base_dir"),
        ):
            service.SvcDoRun()

        service._shut_down.assert_called_once_with(process, ANY)
        services_module.consume_restart_request.assert_called_once()
        service._start_frigate.assert_called_once()

    def test_clean_child_exit_stops_the_service(self):
        service = self._service()
        process = Mock(returncode=0)
        service._start_frigate = Mock(return_value=process)
        service._wait_for_exit = Mock()
        services_module = ModuleType("frigate.util.services")
        services_module.consume_restart_request = Mock(return_value=False)

        with (
            patch.dict(sys.modules, {"frigate.util.services": services_module}),
            patch.object(windows_service, "log_base_dir"),
        ):
            service.SvcDoRun()

        service._start_frigate.assert_called_once()
        service._wait_for_exit.assert_called_once_with(process, ANY)

    def test_unrequested_child_failure_fails_the_service(self):
        service = self._service()
        process = Mock(returncode=1)
        service._start_frigate = Mock(return_value=process)
        service._wait_for_exit = Mock()
        services_module = ModuleType("frigate.util.services")
        services_module.consume_restart_request = Mock(return_value=False)

        with (
            patch.dict(sys.modules, {"frigate.util.services": services_module}),
            patch.object(windows_service, "log_base_dir"),
            self.assertRaisesRegex(FrigateServiceFailure, "exited with code 1"),
        ):
            service.SvcDoRun()

        service._start_frigate.assert_called_once()
        service._wait_for_exit.assert_called_once_with(process, ANY)


class TestFrigateServiceShutdown(unittest.TestCase):
    def _service(self) -> FrigateService:
        return FrigateService.__new__(FrigateService)

    def test_child_that_exits_within_grace_period_is_not_terminated(self):
        service = self._service()
        process = Mock()
        process.poll.return_value = None
        logger = Mock()

        service._shut_down(process, logger)

        process.wait.assert_called_once_with(timeout=windows_service.STOP_GRACE_SECONDS)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_child_is_terminated_then_killed_only_after_each_timeout(self):
        service = self._service()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            sp.TimeoutExpired("frigate", windows_service.STOP_GRACE_SECONDS),
            sp.TimeoutExpired("frigate", windows_service.TERMINATE_GRACE_SECONDS),
            None,
        ]

        service._shut_down(process, Mock())

        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(
            [call.args for call in process.wait.call_args_list],
            [
                (),
                (),
                (),
            ],
        )
        self.assertEqual(
            [call.kwargs for call in process.wait.call_args_list],
            [
                {"timeout": windows_service.STOP_GRACE_SECONDS},
                {"timeout": windows_service.TERMINATE_GRACE_SECONDS},
                {},
            ],
        )


if __name__ == "__main__":
    unittest.main()
