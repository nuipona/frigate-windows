"""Tests for graceful FFmpeg shutdown on native Windows."""

import logging
import signal
import subprocess as sp
import unittest
from unittest.mock import MagicMock, patch

from frigate.util import ffmpeg as ffmpeg_util

logger = logging.getLogger(__name__)


def _process(stdin=None, wait_side_effect=None) -> MagicMock:
    process = MagicMock()
    process.stdin = stdin
    process.pid = 4242
    process.wait.side_effect = wait_side_effect
    return process


class TestRequestFfmpegStop(unittest.TestCase):
    def test_writes_the_quit_command_and_waits(self):
        stdin = MagicMock()
        process = _process(stdin=stdin)

        with patch.object(ffmpeg_util.os, "kill") as mock_kill:
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        stdin.write.assert_called_once_with(b"q")
        stdin.flush.assert_called_once()
        process.wait.assert_called_once_with(timeout=ffmpeg_util.FFMPEG_QUIT_POLL)
        process.terminate.assert_not_called()
        mock_kill.assert_not_called()

    def test_can_skip_the_quit_poll_before_breaking(self):
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 0))

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill") as mock_kill,
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger, wait_timeout=0.0)

        process.wait.assert_called_once_with(timeout=0.0)
        mock_kill.assert_called_once_with(4242, 21)
        process.terminate.assert_not_called()

    def test_falls_back_to_a_break_event(self):
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 1))

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill") as mock_kill,
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        mock_kill.assert_called_once_with(4242, 21)
        process.terminate.assert_not_called()

    def test_falls_back_to_terminate_without_a_break_event(self):
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 1))

        with patch.object(signal, "CTRL_BREAK_EVENT", None, create=True):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()

    def test_falls_back_to_terminate_when_stdin_is_gone(self):
        stdin = MagicMock()
        stdin.write.side_effect = BrokenPipeError("gone")
        process = _process(stdin=stdin)

        with patch.object(signal, "CTRL_BREAK_EVENT", None, create=True):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()

    def test_falls_back_to_terminate_when_break_event_fails(self):
        stdin = MagicMock()
        process = _process(stdin=stdin, wait_side_effect=sp.TimeoutExpired("ffmpeg", 1))

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill", side_effect=OSError("no console")),
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        process.terminate.assert_called_once()

    def test_without_stdin_goes_straight_to_break_event(self):
        process = _process(stdin=None)

        with (
            patch.object(signal, "CTRL_BREAK_EVENT", 21, create=True),
            patch.object(ffmpeg_util.os, "kill") as mock_kill,
        ):
            ffmpeg_util.request_ffmpeg_stop(process, logger)

        mock_kill.assert_called_once_with(4242, 21)
        process.terminate.assert_not_called()


class TestFfmpegPopenKwargs(unittest.TestCase):
    def test_starts_ffmpeg_in_its_own_process_group(self):
        with patch.object(ffmpeg_util.sp, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            kwargs = ffmpeg_util.ffmpeg_popen_kwargs()

        self.assertEqual(kwargs, {"creationflags": 512})


class TestStopFfmpeg(unittest.TestCase):
    @patch.object(ffmpeg_util, "request_ffmpeg_stop")
    def test_stops_and_reaps_the_process(self, request_stop):
        process = MagicMock()

        ffmpeg_util.stop_ffmpeg(process, logger)

        request_stop.assert_called_once_with(process, logger)
        process.communicate.assert_called_once_with(
            timeout=ffmpeg_util.FFMPEG_STOP_TIMEOUT
        )
        process.kill.assert_not_called()

    @patch.object(ffmpeg_util, "request_ffmpeg_stop")
    def test_kills_a_process_that_will_not_stop(self, _request_stop):
        process = MagicMock()
        process.communicate.side_effect = [sp.TimeoutExpired("ffmpeg", 1), None]

        ffmpeg_util.stop_ffmpeg(process, logger)

        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
