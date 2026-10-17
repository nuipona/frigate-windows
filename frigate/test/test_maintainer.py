import datetime
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psutil

# Mock complex imports before importing maintainer, saving originals so we can
# restore them after import and avoid polluting sys.modules for other tests.
_MOCKED_MODULES = [
    "frigate.comms.inter_process",
    "frigate.comms.detections_updater",
    "frigate.comms.recordings_updater",
    "frigate.config.camera.updater",
]
_originals = {name: sys.modules.get(name) for name in _MOCKED_MODULES}
for name in _MOCKED_MODULES:
    sys.modules[name] = MagicMock()

# Now import the class under test
from frigate.config import FrigateConfig, RetainModeEnum  # noqa: E402
from frigate.record.maintainer import (  # noqa: E402
    RecordingMaintainer,
    cache_files_in_use,
)

# Restore original modules (or remove mock if there was no original)
for name, orig in _originals.items():
    if orig is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = orig


def _tmp_path(*parts: str) -> str:
    """Native temp path so fixtures carry the separators the OS carries."""
    return os.path.join(tempfile.gettempdir(), *parts)


class TestMaintainer(unittest.IsolatedAsyncioTestCase):
    async def test_move_files_survives_bad_filename(self):
        config = MagicMock(spec=FrigateConfig)
        config.cameras = {}
        stop_event = MagicMock()

        maintainer = RecordingMaintainer(config, stop_event)

        # We need to mock end_time_cache to avoid key errors if logic proceeds
        maintainer.end_time_cache = {}

        # Mock filesystem
        # One bad file, one good file
        files = ["bad_filename.mp4", "camera@20210101000000+0000.mp4"]

        with patch("os.listdir", return_value=files):
            with patch("os.path.isfile", return_value=True):
                with patch(
                    "frigate.record.maintainer.psutil.process_iter", return_value=[]
                ):
                    with patch("frigate.record.maintainer.logger.warning") as warn:
                        # Mock validate_and_move_segment to avoid further logic.
                        # The requestor is real when another test imported the
                        # maintainer first, and it would block on a reply.
                        maintainer.validate_and_move_segment = AsyncMock()
                        maintainer.requestor = MagicMock()

                        try:
                            await maintainer.move_files()
                        except ValueError as e:
                            if "not enough values to unpack" in str(e):
                                self.fail("move_files() crashed on bad filename!")
                            raise e
                        except Exception:
                            # Ignore other errors (like DB connection) as we only care about the unpack crash
                            pass

                        # The bad filename is encountered in multiple loops, but should only warn once.
                        matching = [
                            c
                            for c in warn.call_args_list
                            if c.args
                            and isinstance(c.args[0], str)
                            and "Skipping unexpected files in cache" in c.args[0]
                        ]
                        self.assertEqual(
                            1,
                            len(matching),
                            f"Expected a single warning for unexpected files, got {len(matching)}",
                        )

    async def test_drops_quiet_segment_when_only_motion_retention(self):
        # Regression: when motion retention is enabled but a segment has no
        # motion and no review overlaps it, the segment must still be dropped.
        # Otherwise it sits in cache forever, accumulates, and triggers the
        # "Unable to keep up with recording segments in cache" warning every
        # ~10s as the overflow trim in move_files discards the oldest one.
        config = MagicMock(spec=FrigateConfig)

        camera_config = MagicMock()
        camera_config.record.enabled = True
        camera_config.record.continuous.days = 0
        camera_config.record.motion.days = 1
        camera_config.record.event_pre_capture = 5
        config.cameras = {"test_cam": camera_config}

        stop_event = MagicMock()
        maintainer = RecordingMaintainer(config, stop_event)

        now = datetime.datetime.now(datetime.UTC)
        start_time = now - datetime.timedelta(seconds=20)
        end_time = now - datetime.timedelta(seconds=10)
        cache_path = _tmp_path("cache", "test_cam@20260417150000+0000.mp4")

        maintainer.end_time_cache = {
            cache_path: (end_time, 10.0, None, None, None, None, None)
        }
        # Single processed frame well past end_time with no motion/objects.
        maintainer.object_recordings_info["test_cam"] = [(now.timestamp(), [], [], [])]
        maintainer.audio_recordings_info["test_cam"] = []

        maintainer.drop_segment = MagicMock()
        maintainer.recordings_publisher = MagicMock()

        result = await maintainer.validate_and_move_segment(
            "test_cam",
            reviews=[],
            recording={
                "start_time": start_time,
                "cache_path": cache_path,
                "stream_type": "main",
            },
        )

        self.assertIsNone(result)
        maintainer.drop_segment.assert_called_once_with(cache_path)

    async def test_defers_review_overlap_segment_until_metadata_catches_up(self):
        # Regression: a segment overlapping an active_objects review must not
        # be dropped while detection metadata lags behind the segment end,
        # the missing frames may hold the active objects (or continuous
        # retention would keep it anyway).
        config = MagicMock(spec=FrigateConfig)

        camera_config = MagicMock()
        camera_config.record.enabled = True
        camera_config.record.continuous.days = 7
        camera_config.record.motion.days = 0
        camera_config.record.alerts.retain.mode = RetainModeEnum.active_objects
        camera_config.record.get_review_pre_capture.return_value = 5
        camera_config.record.get_review_post_capture.return_value = 5
        config.cameras = {"test_cam": camera_config}

        stop_event = MagicMock()
        maintainer = RecordingMaintainer(config, stop_event)

        now = datetime.datetime.now(datetime.UTC)
        start_time = now - datetime.timedelta(seconds=20)
        end_time = now - datetime.timedelta(seconds=10)
        cache_path = _tmp_path("cache", "test_cam@20260417150000+0000.mp4")

        maintainer.end_time_cache = {
            cache_path: (end_time, 10.0, False, None, None, None, [])
        }
        # Metadata has only reached partway into the segment.
        maintainer.object_recordings_info["test_cam"] = [
            (end_time.timestamp() - 8, [], [], [])
        ]
        maintainer.audio_recordings_info["test_cam"] = []

        maintainer.drop_segment = MagicMock()
        maintainer.move_segment = AsyncMock(return_value=None)
        maintainer.recordings_publisher = MagicMock()

        review = MagicMock()
        review.severity = "alert"
        review.start_time = start_time.timestamp() - 30
        review.end_time = None

        result = await maintainer.validate_and_move_segment(
            "test_cam",
            reviews=[review],
            recording={
                "start_time": start_time,
                "cache_path": cache_path,
                "stream_type": "main",
            },
        )

        self.assertIsNone(result)
        maintainer.drop_segment.assert_not_called()
        maintainer.move_segment.assert_not_awaited()

        # Once metadata passes the segment end, continuous retention keeps it.
        maintainer.object_recordings_info["test_cam"].append(
            (now.timestamp(), [], [], [])
        )

        await maintainer.validate_and_move_segment(
            "test_cam",
            reviews=[review],
            recording={
                "start_time": start_time,
                "cache_path": cache_path,
                "stream_type": "main",
            },
        )

        maintainer.drop_segment.assert_not_called()
        maintainer.move_segment.assert_awaited_once()

    async def test_waits_for_detection_when_no_frame_has_been_processed(self):
        # Regression: a segment can be written before the first detection
        # frame, so the frame the retention decision is based on is the epoch.
        # That has to mean "not ready yet" and leave the segment in the cache:
        # reading the timestamp through local time raises OSError [Errno 22]
        # on Windows, and the segment was then dropped from the cycle.
        config = MagicMock(spec=FrigateConfig)

        camera_config = MagicMock()
        camera_config.record.enabled = True
        config.cameras = {"test_cam": camera_config}

        stop_event = MagicMock()
        maintainer = RecordingMaintainer(config, stop_event)

        now = datetime.datetime.now(datetime.UTC)
        cache_path = _tmp_path("cache", "test_cam@20260417150000+0000.mp4")

        maintainer.end_time_cache = {
            cache_path: (
                now - datetime.timedelta(seconds=10),
                10.0,
                False,
                None,
                None,
                None,
                [],
            )
        }
        maintainer.object_recordings_info["test_cam"] = []
        maintainer.audio_recordings_info["test_cam"] = []

        maintainer.drop_segment = MagicMock()
        maintainer.move_segment = AsyncMock(return_value=None)
        maintainer.recordings_publisher = MagicMock()

        result = await maintainer.validate_and_move_segment(
            "test_cam",
            reviews=[],
            recording={
                "start_time": now - datetime.timedelta(seconds=20),
                "cache_path": cache_path,
                "stream_type": "main",
            },
        )

        self.assertIsNone(result)
        maintainer.drop_segment.assert_not_called()
        maintainer.move_segment.assert_not_awaited()

    async def test_expire_stale_recordings_info_drops_only_absent_cameras(self):
        config = MagicMock(spec=FrigateConfig)
        config.cameras = {}
        stop_event = MagicMock()
        maintainer = RecordingMaintainer(config, stop_event)

        now = datetime.datetime.now().timestamp()
        ancient = now - 86400
        recent = now - 1

        maintainer.object_recordings_info["present_cam"] = [(ancient, [], [], [])]
        maintainer.audio_recordings_info["present_cam"] = [(ancient, 0, [])]

        maintainer.object_recordings_info["absent_cam"] = [
            (ancient, [], [], []),
            (recent, [], [], []),
        ]
        maintainer.audio_recordings_info["absent_cam"] = [
            (ancient, 0, []),
            (recent, 0, []),
        ]

        # keyed by (camera, stream_type), matching what move_files passes
        grouped_recordings = {("present_cam", "main"): [{"start_time": ancient}]}

        maintainer._expire_stale_recordings_info(grouped_recordings)

        self.assertEqual(
            maintainer.object_recordings_info["present_cam"], [(ancient, [], [], [])]
        )
        self.assertEqual(
            maintainer.audio_recordings_info["present_cam"], [(ancient, 0, [])]
        )

        self.assertEqual(
            maintainer.object_recordings_info["absent_cam"], [(recent, [], [], [])]
        )
        self.assertEqual(
            maintainer.audio_recordings_info["absent_cam"], [(recent, 0, [])]
        )


class TestCacheFilesInUse(unittest.TestCase):
    """The segment ffmpeg is still writing must not look like a finished one.

    A segment that has not been closed yet has no moov atom, so it probes as a
    file with no video stream. If the maintainer cannot tell that ffmpeg still
    has it open, it discards the segment that is still being written, which is
    what makes Windows recordings disappear.
    """

    def _process(self, name: str, paths: list[str]) -> MagicMock:
        process = MagicMock()
        process.name.return_value = name
        process.open_files.return_value = [SimpleNamespace(path=path) for path in paths]
        return process

    def _in_use(self, cache_dir: str, processes: list[MagicMock]) -> set[str]:
        with patch(
            "frigate.record.maintainer.psutil.process_iter", return_value=processes
        ):
            return cache_files_in_use(cache_dir)

    def test_a_windows_ffmpeg_is_recognised(self):
        process = self._process("ffmpeg.exe", [r"C:\cache\e2e@20260101000000+0000.mp4"])

        self.assertEqual(
            self._in_use(r"C:\cache", [process]), {"e2e@20260101000000+0000.mp4"}
        )

    def test_a_posix_ffmpeg_is_recognised(self):
        process = self._process(
            "ffmpeg", [_tmp_path("cache", "e2e@20260101000000+0000.mp4")]
        )

        self.assertEqual(
            self._in_use(_tmp_path("cache"), [process]), {"e2e@20260101000000+0000.mp4"}
        )

    def test_other_processes_and_other_files_are_ignored(self):
        processes = [
            self._process("python.exe", [r"C:\cache\e2e@1.mp4"]),
            self._process("ffmpeg.exe", [r"C:\config\config.yml"]),
        ]

        self.assertEqual(self._in_use(r"C:\cache", processes), set())

    def test_a_process_that_cannot_be_inspected_is_skipped(self):
        process = MagicMock()
        process.name.side_effect = psutil.AccessDenied()

        self.assertEqual(self._in_use(_tmp_path("cache"), [process]), set())


if __name__ == "__main__":
    unittest.main()
