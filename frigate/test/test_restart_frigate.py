"""Tests for restarting frigate under s6."""

import os
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psutil

from frigate.util.platform import IS_WINDOWS
from frigate.util.services import RESTART_MARKER_NAME, restart_frigate


class TestRestartFrigate(unittest.TestCase):
    def _s6_process(self) -> MagicMock:
        proc = MagicMock()
        proc.name.return_value = "s6-svscan"
        return proc

    @patch("frigate.util.services.os.kill")
    @patch("frigate.util.services.psutil.Process")
    @unittest.skipIf(
        IS_WINDOWS,
        "Windows exits through raise_signal instead of signalling a supervisor",
    )
    def test_terminates_s6_when_permitted(self, mock_process, mock_kill):
        proc = self._s6_process()
        mock_process.return_value = proc

        restart_frigate()

        proc.terminate.assert_called_once()
        mock_kill.assert_not_called()

    @patch("frigate.util.services.os.getpid", return_value=99)
    @patch("frigate.util.services.os.kill")
    @patch("frigate.util.services.psutil.Process")
    @unittest.skipIf(
        IS_WINDOWS,
        "Windows exits through raise_signal instead of signalling a supervisor",
    )
    def test_exits_self_when_s6_signal_is_denied(
        self, mock_process, mock_kill, _mock_getpid
    ):
        """Running unprivileged, frigate cannot signal root's s6-svscan."""
        proc = self._s6_process()
        proc.terminate.side_effect = psutil.AccessDenied(pid=1, name="s6-svscan")
        mock_process.return_value = proc

        restart_frigate()

        mock_kill.assert_called_once_with(99, signal.SIGINT)

    @patch("frigate.util.services.os.getpid", return_value=99)
    @patch("frigate.util.services.os.kill")
    @patch("frigate.util.services.psutil.Process")
    @unittest.skipIf(
        IS_WINDOWS,
        "Windows exits through raise_signal instead of signalling a supervisor",
    )
    def test_exits_self_without_s6(self, mock_process, mock_kill, _mock_getpid):
        proc = MagicMock()
        proc.name.return_value = "init"
        mock_process.return_value = proc

        restart_frigate()

        proc.terminate.assert_not_called()
        mock_kill.assert_called_once_with(99, signal.SIGINT)

    @patch("frigate.util.services.IS_WINDOWS", True)
    @patch("frigate.util.services.os.getpid", return_value=99)
    @patch("frigate.util.services.os.kill")
    @patch("frigate.util.services.signal.raise_signal")
    @patch("frigate.util.services.psutil.Process")
    def test_windows_raises_the_signal_instead_of_killing(
        self, mock_process, mock_raise, mock_kill, _mock_getpid
    ):
        """Windows os.kill is TerminateProcess for anything but a console event.

        os.kill(pid, SIGINT) there does not run the SIGINT handler, it kills
        the process outright, which skips every shutdown path Frigate has and
        orphans all of its children.
        """
        restart_frigate()

        mock_raise.assert_called_once_with(signal.SIGINT)
        mock_kill.assert_not_called()
        mock_process.assert_not_called()

    @patch("frigate.util.services.get_tmp_dir")
    @patch("frigate.util.services.IS_WINDOWS", True)
    @patch("frigate.util.services.signal.raise_signal")
    def test_windows_restart_writes_the_launcher_marker(self, _mock_raise, mock_tmp):
        """The launcher restarts Frigate only when the marker is on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_tmp.return_value = tmp

            restart_frigate()

            self.assertTrue(
                os.path.isfile(os.path.join(tmp, RESTART_MARKER_NAME)),
            )

    @patch("frigate.util.services.get_tmp_dir")
    @patch("frigate.util.services.IS_WINDOWS", False)
    @patch("frigate.util.services.os.kill")
    @patch("frigate.util.services.psutil.Process")
    def test_posix_restart_does_not_write_the_launcher_marker(
        self, mock_process, _mock_kill, mock_tmp
    ):
        """In the container s6 owns the restart, so no marker is written."""
        proc = MagicMock()
        proc.name.return_value = "init"
        mock_process.return_value = proc

        with tempfile.TemporaryDirectory() as tmp:
            mock_tmp.return_value = tmp

            restart_frigate()

            self.assertFalse(
                os.path.isfile(os.path.join(tmp, RESTART_MARKER_NAME)),
            )

    @patch("frigate.util.services.get_tmp_dir")
    @patch("frigate.util.services.IS_WINDOWS", True)
    @patch("frigate.util.services.signal.raise_signal")
    def test_unwritable_restart_marker_does_not_stop_the_shutdown(
        self, _mock_raise, mock_tmp
    ):
        """A launcher without its marker leaves Frigate stopped, which the
        user recovers from by hand, so the write failure is not fatal."""
        with tempfile.TemporaryDirectory() as tmp:
            mock_tmp.return_value = tmp

            # a directory where the marker file must be created makes the
            # write fail without needing an unreadable tmp dir
            os.mkdir(os.path.join(tmp, RESTART_MARKER_NAME))

            restart_frigate()
