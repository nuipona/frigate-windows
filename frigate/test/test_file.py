import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import cv2
import numpy as np

from frigate.util import file as file_util


class TestFileUtils(TestCase):
    def _write_clean_snapshot(
        self, clips_dir: str, event_id: str, image: np.ndarray
    ) -> None:
        assert cv2.imwrite(
            os.path.join(clips_dir, f"front_door-{event_id}-clean.webp"),
            image,
        )

    def test_get_event_snapshot_bytes_reads_clean_webp(self):
        event_id = "clean-webp"
        image = np.zeros((100, 200, 3), np.uint8)
        event = SimpleNamespace(
            id=event_id,
            camera="front_door",
            label="Mock",
            top_score=100,
            score=0,
            start_time=0,
            data={
                "box": [0.25, 0.25, 0.25, 0.5],
                "score": 0.85,
                "attributes": [],
            },
        )

        with (
            tempfile.TemporaryDirectory() as clips_dir,
            patch.object(file_util, "CLIPS_DIR", clips_dir),
        ):
            self._write_clean_snapshot(clips_dir, event_id, image)

            snapshot_image, is_clean = file_util.load_event_snapshot_image(
                event, clean_only=True
            )

            assert is_clean
            assert snapshot_image is not None
            assert snapshot_image.shape[:2] == image.shape[:2]

            rendered_bytes, _ = file_util.get_event_snapshot_bytes(
                event,
                ext="jpg",
                timestamp=False,
                bounding_box=True,
                crop=False,
                height=40,
                quality=None,
                timestamp_style=None,
                colormap={},
            )
            assert rendered_bytes is not None

            rendered_image = cv2.imdecode(
                np.frombuffer(rendered_bytes, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            assert rendered_image is not None
            assert rendered_image.shape[0] == 40
            assert rendered_image.max() > 0

    def test_get_event_thumbnail_bytes_ignores_empty_file(self):
        """Verify empty thumbnail files are treated as missing."""
        event = SimpleNamespace(id="empty-thumb", camera="front_door", thumbnail=None)

        with (
            tempfile.TemporaryDirectory() as thumb_dir,
            patch.object(file_util, "THUMB_DIR", thumb_dir),
        ):
            camera_dir = os.path.join(thumb_dir, event.camera)
            os.makedirs(camera_dir)

            with open(os.path.join(camera_dir, f"{event.id}.webp"), "wb"):
                pass

            assert file_util.get_event_thumbnail_bytes(event) is None


class TestTrimOldestFiles(TestCase):
    def _fill(self, folder: str, names: list[str]) -> None:
        # a short gap keeps the ctime order deterministic
        for name in names:
            with open(os.path.join(folder, name), "wb"):
                pass

            time.sleep(0.01)

    def test_trims_folder_already_over_limit(self):
        """Verify one call trims a folder that is far over the limit."""
        with tempfile.TemporaryDirectory() as folder:
            self._fill(folder, [f"{i:03d}.webp" for i in range(10)])

            file_util.trim_oldest_files(folder, 4)

            assert sorted(os.listdir(folder)) == [
                "006.webp",
                "007.webp",
                "008.webp",
                "009.webp",
            ]

    def test_counts_every_listed_image_extension(self):
        """Verify the trim counts the same images the train listing shows."""
        with tempfile.TemporaryDirectory() as folder:
            self._fill(
                folder, ["notes.txt", "a.jpg", "b.jpeg", "c.png", "d.webp", "e.webp"]
            )

            file_util.trim_oldest_files(folder, 2)

            assert sorted(os.listdir(folder)) == ["d.webp", "e.webp", "notes.txt"]

    def test_file_removed_during_scan_does_not_skip_trim(self):
        """Verify a file deleted between listing and stat still trims the rest."""
        real_listdir = os.listdir

        with tempfile.TemporaryDirectory() as folder:
            self._fill(folder, [f"{i:03d}.webp" for i in range(10)])

            with patch(
                "os.listdir", side_effect=lambda p: real_listdir(p) + ["gone.webp"]
            ):
                file_util.trim_oldest_files(folder, 4)

            assert len(os.listdir(folder)) == 4


class TestRetryingFileOperations(TestCase):
    """File operations retry while another process holds the file.

    Windows Defender and the search indexer open every file Frigate writes,
    which turns a move or a delete into a permission error that succeeds on the
    next attempt.
    """

    def test_sharing_violation_is_retried(self):
        calls = {"count": 0}
        real_remove = os.remove

        def flaky_remove(path):
            calls["count"] += 1

            if calls["count"] < 3:
                error = PermissionError(13, "in use", path)
                error.winerror = 32
                raise error

            real_remove(path)

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "segment.mp4")
            open(path, "w").close()

            with (
                patch.object(file_util.os, "remove", side_effect=flaky_remove),
                patch.object(file_util.time, "sleep"),
            ):
                file_util.robust_remove(path)

            assert calls["count"] == 3
            assert not os.path.exists(path)

    def test_a_permanent_error_is_raised_immediately(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "missing.mp4")

            with patch.object(file_util.time, "sleep") as sleep:
                self.assertRaises(FileNotFoundError, file_util.robust_remove, path)

            sleep.assert_not_called()

    def test_transient_errors_are_identified_per_platform(self):
        sharing_violation = PermissionError(13, "in use", "f.mp4")
        sharing_violation.winerror = 32
        self.assertTrue(file_util.is_held_by_another_process(sharing_violation))

        lock_violation = PermissionError(13, "locked", "f.mp4")
        lock_violation.winerror = 33
        self.assertTrue(file_util.is_held_by_another_process(lock_violation))

        self.assertFalse(
            file_util.is_held_by_another_process(FileNotFoundError(2, "gone", "f.mp4"))
        )

    def test_move_retries_and_completes(self):
        calls = {"count": 0}
        real_move = file_util.shutil.move

        def flaky_move(src, dst):
            calls["count"] += 1

            if calls["count"] < 2:
                error = PermissionError(13, "in use", src)
                error.winerror = 32
                raise error

            real_move(src, dst)

        with tempfile.TemporaryDirectory() as folder:
            src = os.path.join(folder, "src")
            dst = os.path.join(folder, "dst")
            os.makedirs(src)
            open(os.path.join(src, "frame.webp"), "w").close()

            with (
                patch.object(file_util.shutil, "move", side_effect=flaky_move),
                patch.object(file_util.time, "sleep"),
            ):
                file_util.robust_move(src, dst)

            assert os.path.isdir(dst)
            assert not os.path.exists(src)

    def test_async_remove_retries_without_blocking(self):
        import asyncio

        calls = {"count": 0}
        real_remove = os.remove

        def flaky_remove(path):
            calls["count"] += 1

            if calls["count"] < 2:
                error = PermissionError(13, "in use", path)
                error.winerror = 32
                raise error

            real_remove(path)

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "segment.mp4")
            open(path, "w").close()

            with (
                patch.object(file_util.os, "remove", side_effect=flaky_remove),
                patch.object(file_util.asyncio, "sleep", new=_noop_sleep),
            ):
                asyncio.run(file_util.async_robust_remove(path))

            assert not os.path.exists(path)


async def _noop_sleep(*_args, **_kwargs) -> None:
    return


class TestFileLockAcrossProcesses(TestCase):
    """FileLock against a genuinely separate interpreter process.

    In-process locking exercises neither the Windows byte range semantics nor
    what happens to a lock whose holder died, which is exactly the code that
    looks fine on Linux and quietly fails to serialize on Windows.
    """

    holder_script = """
import json
import sys
import time

from frigate.util.file import FileLock

mode, lock_path, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
lock = FileLock(lock_path, timeout=int(sys.argv[4]), poll_interval=0.05)

if mode == "hold":
    if lock.acquire():
        with open(result_path, "w", encoding="utf-8") as out:
            json.dump("held", out)
        # the parent kills this process without a release, to check that a
        # dead holder's lock can be taken by a new process
        time.sleep(120)
    sys.exit(1)

acquired = lock.acquire()

with open(result_path, "w", encoding="utf-8") as out:
    json.dump("acquired" if acquired else "denied", out)
"""

    def _spawn(self, mode: str, lock_path: str, result_path: str, timeout: int):
        import subprocess
        import sys as _sys

        from frigate.util.file import __file__ as file_module_path

        repo_root = str(Path(file_module_path).resolve().parents[2])
        env = {**os.environ, "PYTHONPATH": repo_root}

        return subprocess.Popen(
            [
                _sys.executable,
                "-c",
                self.holder_script,
                mode,
                lock_path,
                result_path,
                str(timeout),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _read_result(self, result_path: str) -> str:
        deadline = time.time() + 30

        while time.time() < deadline:
            try:
                with open(result_path, encoding="utf-8") as file:
                    return json.load(file)
            except (OSError, ValueError):
                time.sleep(0.05)

        self.fail("the child process never wrote its result")

    def test_a_second_process_cannot_take_a_held_lock(self):
        from frigate.util.file import FileLock

        with tempfile.TemporaryDirectory() as folder:
            lock_path = os.path.join(folder, "resource.lock")
            result_path = os.path.join(folder, "result.json")

            with FileLock(lock_path, timeout=5):
                child = self._spawn("try", lock_path, result_path, 1)
                try:
                    self.assertEqual(self._read_result(result_path), "denied")
                finally:
                    child.wait(timeout=30)

    def test_a_lock_is_takeable_once_its_holder_exits(self):
        from frigate.util.file import FileLock

        with tempfile.TemporaryDirectory() as folder:
            lock_path = os.path.join(folder, "resource.lock")
            result_path = os.path.join(folder, "result.json")

            holder = self._spawn("hold", lock_path, result_path, 5)
            try:
                self.assertEqual(self._read_result(result_path), "held")
            finally:
                # a hard kill, the way a crashed process dies: no release()
                holder.kill()
                holder.wait(timeout=30)

            with FileLock(lock_path, timeout=10):
                pass

    def test_a_released_lock_can_be_taken_by_another_process(self):
        from frigate.util.file import FileLock

        with tempfile.TemporaryDirectory() as folder:
            lock_path = os.path.join(folder, "resource.lock")
            result_path = os.path.join(folder, "result.json")

            with FileLock(lock_path, timeout=5):
                pass

            child = self._spawn("try", lock_path, result_path, 10)
            try:
                self.assertEqual(self._read_result(result_path), "acquired")
            finally:
                child.wait(timeout=30)
