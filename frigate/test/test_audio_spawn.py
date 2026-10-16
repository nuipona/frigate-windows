"""Tests for audio maintainer spawning on runtime camera adds."""

import threading
import unittest
from unittest.mock import MagicMock, patch

from frigate.config import FrigateConfig
from frigate.events.audio import (
    AUDIO_PROCESS_SHUTDOWN_TIMEOUT,
    AUDIO_THREAD_SHUTDOWN_TIMEOUT,
    AudioEventMaintainer,
    AudioProcessor,
    join_audio_process,
)
from frigate.log import LogPipe


def _build_config() -> FrigateConfig:
    return FrigateConfig(
        **{
            "mqtt": {"host": "mqtt"},
            "cameras": {
                "front_door": {
                    "ffmpeg": {
                        "inputs": [
                            {
                                "path": "rtsp://10.0.0.1:554/video",
                                "roles": ["detect", "audio"],
                            }
                        ]
                    },
                    "audio": {"enabled": True},
                }
            },
        }
    )


class TestAudioSpawnIfNeeded(unittest.TestCase):
    def _processor(self, config: FrigateConfig, camera_metrics: dict) -> AudioProcessor:
        processor = AudioProcessor.__new__(AudioProcessor)
        processor.config = config
        processor.camera_metrics = camera_metrics
        processor.embeddings_metrics = MagicMock()
        processor.audio_threads = {}
        processor.transcription_model_runner = None
        processor.genai_manager = None
        processor.stop_event = threading.Event()
        processor.logger = MagicMock()
        return processor

    def test_skips_camera_whose_metrics_have_not_been_created_yet(self):
        """The camera maintainer creates metrics on its own poll of the add
        update, so the audio processor can see the camera first."""
        config = _build_config()
        processor = self._processor(config, {})

        with patch("frigate.events.audio.AudioEventMaintainer") as maintainer:
            processor.spawn_if_needed(config.cameras["front_door"])

        maintainer.assert_not_called()
        assert processor.audio_threads == {}

    def test_spawns_once_metrics_exist_and_passes_the_metrics_object(self):
        config = _build_config()
        metrics = object()
        processor = self._processor(config, {"front_door": metrics})

        with patch("frigate.events.audio.AudioEventMaintainer") as maintainer:
            processor.spawn_if_needed(config.cameras["front_door"])

        assert maintainer.call_args.args[2] is metrics
        assert "front_door" in processor.audio_threads
        maintainer.return_value.start.assert_called_once()

    def test_does_not_respawn_for_a_camera_already_running(self):
        config = _build_config()
        processor = self._processor(config, {"front_door": object()})
        processor.audio_threads["front_door"] = MagicMock()

        with patch("frigate.events.audio.AudioEventMaintainer") as maintainer:
            processor.spawn_if_needed(config.cameras["front_door"])

        maintainer.assert_not_called()

    def test_skips_camera_whose_ffmpeg_update_has_not_arrived_yet(self):
        """The add update carries the camera before its ffmpeg inputs are
        applied, so the audio role can be briefly missing."""
        config = _build_config()
        camera = config.cameras["front_door"]
        camera.ffmpeg.inputs[0].roles = ["detect"]
        processor = self._processor(config, {"front_door": object()})

        with patch("frigate.events.audio.AudioEventMaintainer") as maintainer:
            processor.spawn_if_needed(camera)

        maintainer.assert_not_called()

    def test_windows_audio_thread_is_daemonized(self):
        config = _build_config()
        processor = self._processor(config, {"front_door": object()})

        with (
            patch("frigate.events.audio.IS_WINDOWS", True),
            patch("frigate.events.audio.AudioEventMaintainer") as maintainer,
        ):
            processor.spawn_if_needed(config.cameras["front_door"])

        self.assertTrue(maintainer.return_value.daemon)

    def test_stop_unblocks_ffmpeg_audio_read(self):
        maintainer = AudioEventMaintainer.__new__(AudioEventMaintainer)
        maintainer.camera_stop_event = MagicMock()
        maintainer.audio_listener = MagicMock()
        maintainer.logger = MagicMock()

        with (
            patch("frigate.events.audio.IS_WINDOWS", False),
            patch("frigate.events.audio.request_ffmpeg_stop") as stop_ffmpeg,
        ):
            maintainer.stop()

        maintainer.camera_stop_event.set.assert_called_once()
        stop_ffmpeg.assert_called_once_with(
            maintainer.audio_listener, maintainer.logger
        )

    def test_windows_audio_stop_uses_a_nonblocking_quit_poll(self):
        maintainer = AudioEventMaintainer.__new__(AudioEventMaintainer)
        maintainer.camera_stop_event = MagicMock()
        maintainer.audio_listener = MagicMock()
        maintainer.logger = MagicMock()

        with (
            patch("frigate.events.audio.IS_WINDOWS", True),
            patch("frigate.events.audio.request_ffmpeg_stop") as stop_ffmpeg,
        ):
            maintainer.stop()

        maintainer.camera_stop_event.set.assert_called_once()
        stop_ffmpeg.assert_called_once_with(
            maintainer.audio_listener, maintainer.logger, wait_timeout=0.0
        )

    def test_windows_log_pipe_is_daemonized_with_audio_maintainer(self):
        config = _build_config()
        camera = config.cameras["front_door"]

        with (
            patch("frigate.events.audio.IS_WINDOWS", True),
            patch("frigate.events.audio.AudioTfl"),
            patch("frigate.events.audio.LogPipe") as logpipe,
            patch("frigate.events.audio.InterProcessRequestor"),
            patch("frigate.events.audio.CameraConfigUpdateSubscriber"),
            patch("frigate.events.audio.DetectionPublisher"),
        ):
            AudioEventMaintainer(
                camera,
                config,
                MagicMock(),
                MagicMock(),
                None,
                threading.Event(),
            )

        logpipe.assert_called_once_with(f"ffmpeg.{camera.name}.audio", daemon=True)


class TestAudioShutdown(unittest.TestCase):
    def _processor_with_threads(
        self, count: int
    ) -> tuple[AudioProcessor, list[MagicMock]]:
        processor = AudioProcessor.__new__(AudioProcessor)
        processor.logger = MagicMock()
        threads = [MagicMock() for _ in range(count)]
        for index, thread in enumerate(threads):
            thread.name = f"camera-{index}"
            thread.is_alive.return_value = True
        processor.audio_threads = {
            f"camera-{index}": thread for index, thread in enumerate(threads)
        }
        return processor, threads

    def test_windows_thread_joins_share_one_deadline(self):
        processor, threads = self._processor_with_threads(3)

        with (
            patch("frigate.events.audio.IS_WINDOWS", True),
            patch(
                "frigate.events.audio.time.monotonic",
                side_effect=[100.0, 100.0, 104.0, 111.0],
            ),
        ):
            processor._stop_audio_threads()

        self.assertEqual(
            [thread.join.call_args.args[0] for thread in threads],
            [AUDIO_THREAD_SHUTDOWN_TIMEOUT, 7.0, 0.0],
        )
        for thread in threads:
            thread.stop.assert_called_once()

    def test_linux_keeps_the_existing_per_thread_join_budget(self):
        processor, threads = self._processor_with_threads(2)

        with patch("frigate.events.audio.IS_WINDOWS", False):
            processor._stop_audio_threads()

        for thread in threads:
            self.assertEqual(
                [call.args for call in thread.join.call_args_list],
                [(1,), (10,)],
            )
            thread.stop.assert_called_once()

    def test_windows_audio_process_join_is_bounded(self):
        process = MagicMock()
        process.is_alive.return_value = False

        with patch("frigate.events.audio.IS_WINDOWS", True):
            join_audio_process(process)

        process.join.assert_called_once_with(timeout=AUDIO_PROCESS_SHUTDOWN_TIMEOUT)
        process.is_alive.assert_called_once()

    def test_linux_audio_process_join_remains_unbounded(self):
        process = MagicMock()

        with patch("frigate.events.audio.IS_WINDOWS", False):
            join_audio_process(process)

        process.join.assert_called_once_with()
        process.is_alive.assert_not_called()

    def test_log_pipe_can_be_a_daemon_thread(self):
        logpipe = LogPipe("test.audio.logpipe", daemon=True)
        try:
            self.assertTrue(logpipe.daemon)
        finally:
            logpipe.close()

        logpipe.join(timeout=1)
        self.assertFalse(logpipe.is_alive())
