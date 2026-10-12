"""Path and file utilities."""

import asyncio
import base64
import errno
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
from numpy import ndarray

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

from frigate.const import CLIPS_DIR, THUMB_DIR
from frigate.models import Event
from frigate.util.image import get_snapshot_bytes, relative_box_to_absolute
from frigate.util.platform import IS_WINDOWS

logger = logging.getLogger(__name__)


def get_event_thumbnail_bytes(event: Event) -> bytes | None:
    # callers treat empty bytes as a valid image, so normalize them to None
    if event.thumbnail:
        return base64.b64decode(event.thumbnail) or None
    else:
        try:
            with open(
                os.path.join(THUMB_DIR, event.camera, f"{event.id}.webp"), "rb"
            ) as f:
                return f.read() or None
        except Exception:
            return None


def get_event_snapshot(event: Event) -> ndarray | None:
    image, _ = load_event_snapshot_image(event)
    return image


def get_event_snapshot_path(
    event: Event, *, clean_only: bool = False
) -> tuple[str | None, bool]:
    clean_snapshot_paths = [
        os.path.join(CLIPS_DIR, f"{event.camera}-{event.id}-clean.webp"),
        os.path.join(CLIPS_DIR, f"{event.camera}-{event.id}-clean.png"),
    ]

    for image_path in clean_snapshot_paths:
        if os.path.exists(image_path):
            return image_path, True

    snapshot_path = os.path.join(CLIPS_DIR, f"{event.camera}-{event.id}.jpg")
    if not os.path.exists(snapshot_path):
        return None, False

    # Legacy JPG snapshots may already include overlays, so they should never
    # be treated as clean input for additional rendering.
    if clean_only:
        return None, False

    return snapshot_path, False


def load_event_snapshot_image(
    event: Event, *, clean_only: bool = False
) -> tuple[ndarray | None, bool]:
    image_path, is_clean_snapshot = get_event_snapshot_path(
        event, clean_only=clean_only
    )
    if image_path is None:
        return None, False

    image = cv2.imread(image_path)
    if image is None:
        logger.warning("Unable to load snapshot from %s", image_path)
        return None, False

    return image, is_clean_snapshot


def _get_event_snapshot_overlay_boxes(
    frame_shape: tuple[int, ...], event: Event
) -> list[dict[str, Any]]:
    overlay_boxes: list[dict[str, Any]] = []
    draw_data = event.data.get("draw") if event.data else {}
    draw_boxes = draw_data.get("boxes", []) if isinstance(draw_data, dict) else []

    for draw_box in draw_boxes:
        box = relative_box_to_absolute(frame_shape, draw_box.get("box"))
        if box is None:
            continue

        draw_color = draw_box.get("color", (255, 0, 0))
        color = (
            tuple(draw_color) if isinstance(draw_color, (list, tuple)) else (255, 0, 0)
        )
        overlay_boxes.append(
            {
                "box": box,
                "label": event.label,
                "score": draw_box.get("score"),
                "color": color,
            }
        )

    return overlay_boxes


def get_event_snapshot_bytes(
    event: Event,
    *,
    ext: str,
    timestamp: bool = False,
    bounding_box: bool = False,
    crop: bool = False,
    height: int | None = None,
    quality: int | None = None,
    timestamp_style: Any | None = None,
    colormap: dict[str, tuple[int, int, int]] | None = None,
) -> tuple[bytes | None, float]:
    best_frame, is_clean_snapshot = load_event_snapshot_image(event)
    if best_frame is None:
        return None, 0

    frame_time = _get_event_snapshot_frame_time(event)
    box = relative_box_to_absolute(
        best_frame.shape,
        event.data.get("box") if event.data else None,
    )
    overlay_boxes = _get_event_snapshot_overlay_boxes(best_frame.shape, event)

    if (bounding_box or crop or timestamp) and not is_clean_snapshot:
        logger.warning(
            "Unable to fully honor snapshot query parameters for completed event %s because the clean snapshot is unavailable.",
            event.id,
        )

    return get_snapshot_bytes(
        best_frame,
        frame_time,
        ext=ext,
        timestamp=timestamp and is_clean_snapshot,
        bounding_box=bounding_box and is_clean_snapshot,
        crop=crop and is_clean_snapshot,
        height=height,
        quality=quality,
        label=event.label,
        box=box,
        score=_get_event_snapshot_score(event),
        area=_get_event_snapshot_area(event),
        attributes=_get_event_snapshot_attributes(
            best_frame.shape,
            event.data.get("attributes") if event.data else None,
        ),
        color=(colormap or {}).get(event.label, (255, 255, 255)),
        overlay_boxes=overlay_boxes,
        timestamp_style=timestamp_style,
        estimated_speed=_get_event_snapshot_estimated_speed(event),
    )


def _as_timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()

    return float(value)


def _get_event_snapshot_frame_time(event: Event) -> float:
    if event.data:
        snapshot_frame_time = event.data.get("snapshot_frame_time")
        if snapshot_frame_time is not None:
            return _as_timestamp(snapshot_frame_time)

        frame_time = event.data.get("frame_time")
        if frame_time is not None:
            return _as_timestamp(frame_time)

    return _as_timestamp(event.start_time)


def _get_event_snapshot_attributes(
    frame_shape: tuple[int, ...], attributes: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    absolute_attributes: list[dict[str, Any]] = []

    for attribute in attributes or []:
        box = relative_box_to_absolute(frame_shape, attribute.get("box"))
        if box is None:
            continue

        absolute_attributes.append(
            {
                "box": box,
                "label": attribute.get("label", "attribute"),
                "score": attribute.get("score", 0),
            }
        )

    return absolute_attributes


def _get_event_snapshot_score(event: Event) -> float:
    if event.data:
        score = event.data.get("score")
        if score is not None:
            return score

        top_score = event.data.get("top_score")
        if top_score is not None:
            return top_score

    return event.top_score or event.score or 0


def _get_event_snapshot_area(event: Event) -> int | None:
    if event.data:
        area = event.data.get("snapshot_area")
        if area is not None:
            return int(area)

    return None


def _get_event_snapshot_estimated_speed(event: Event) -> float:
    if event.data:
        estimated_speed = event.data.get("snapshot_estimated_speed")
        if estimated_speed is not None:
            return float(estimated_speed)

        average_speed = event.data.get("average_estimated_speed")
        if average_speed is not None:
            return float(average_speed)

    return 0


### Deletion


def delete_event_images(event: Event) -> bool:
    return delete_event_snapshot(event) and delete_event_thumbnail(event)


def delete_event_snapshot(event: Event) -> bool:
    media_name = f"{event.camera}-{event.id}"
    media_path = Path(f"{os.path.join(CLIPS_DIR, media_name)}.jpg")

    try:
        media_path.unlink(missing_ok=True)
        media_path = Path(f"{os.path.join(CLIPS_DIR, media_name)}-clean.webp")
        media_path.unlink(missing_ok=True)
        # also delete clean.png (legacy) for backward compatibility
        media_path = Path(f"{os.path.join(CLIPS_DIR, media_name)}-clean.png")
        media_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def delete_event_thumbnail(event: Event) -> bool:
    if event.thumbnail:
        return True
    else:
        Path(os.path.join(THUMB_DIR, event.camera, f"{event.id}.webp")).unlink(
            missing_ok=True
        )
        return True


### Training Images

TRAINING_IMAGE_EXTENSIONS = (".webp", ".png", ".jpg", ".jpeg")


def trim_oldest_files(folder: str, max_files: int) -> None:
    """Delete the oldest training images until at most max_files remain."""
    try:
        names = os.listdir(folder)
    except OSError:
        return

    files: list[tuple[float, str]] = []

    for name in names:
        if not name.lower().endswith(TRAINING_IMAGE_EXTENSIONS):
            continue

        path = os.path.join(folder, name)

        # the UI can move or delete an image between listdir and stat
        try:
            files.append((os.path.getctime(path), path))
        except OSError:
            continue

    files.sort(reverse=True)

    for _, path in files[max_files:]:
        try:
            os.unlink(path)
        except OSError:
            logger.debug("Unable to delete training image %s", path)


### File Locking


def _try_lock(fd: int) -> bool:
    """Take an exclusive, non blocking lock on an open file descriptor.

    ``fcntl.flock`` is used on POSIX. Windows has no flock; the closest
    equivalent that works across processes is ``msvcrt.locking`` on a one byte
    region, which is why the file is locked at offset zero.
    """
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True

    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True

    return True


def _unlock(fd: int) -> None:
    """Release a lock taken by :func:`_try_lock`."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return

    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class FileLock:
    """
    A file-based lock for coordinating access to resources across processes.

    Uses fcntl.flock() on Linux and msvcrt.locking() on Windows. Supports
    timeouts, stale lock detection, and can be used as a context manager.

    Example:
        ```python
        # Using as a context manager (recommended)
        with FileLock("/path/to/resource.lock", timeout=60):
            # Critical section
            do_something()

        # Manual acquisition and release
        lock = FileLock("/path/to/resource.lock")
        if lock.acquire(timeout=60):
            try:
                do_something()
            finally:
                lock.release()
        ```

    Attributes:
        lock_path: Path to the lock file
        timeout: Maximum time to wait for lock acquisition (seconds)
        poll_interval: Time to wait between lock acquisition attempts (seconds)
        stale_timeout: Time after which a lock is considered stale (seconds)
    """

    def __init__(
        self,
        lock_path: str | Path,
        timeout: int = 300,
        poll_interval: float = 1.0,
        stale_timeout: int = 600,
        cleanup_stale_on_init: bool = False,
    ):
        """
        Initialize a FileLock.

        Args:
            lock_path: Path to the lock file
            timeout: Maximum time to wait for lock acquisition in seconds (default: 300)
            poll_interval: Time to wait between lock attempts in seconds (default: 1.0)
            stale_timeout: Time after which a lock is considered stale in seconds (default: 600)
            cleanup_stale_on_init: Whether to clean up stale locks on initialization (default: False)
        """
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.stale_timeout = stale_timeout
        self._fd: int | None = None
        self._acquired = False

        if cleanup_stale_on_init:
            self._cleanup_stale_lock()

    def _cleanup_stale_lock(self) -> bool:
        """
        Clean up a stale lock file if it exists and is old.

        Returns:
            True if lock was cleaned up, False otherwise
        """
        try:
            if self.lock_path.exists():
                # Check if lock file is older than stale_timeout
                lock_age = time.time() - self.lock_path.stat().st_mtime
                if lock_age > self.stale_timeout:
                    logger.warning(
                        f"Removing stale lock file: {self.lock_path} (age: {lock_age:.1f}s)"
                    )
                    self.lock_path.unlink()
                    return True
        except Exception as e:
            logger.error(f"Error cleaning up stale lock: {e}")

        return False

    def is_stale(self) -> bool:
        """
        Check if the lock file is stale (older than stale_timeout).

        Returns:
            True if lock is stale, False otherwise
        """
        try:
            if self.lock_path.exists():
                lock_age = time.time() - self.lock_path.stat().st_mtime
                return lock_age > self.stale_timeout
        except Exception:
            pass

        return False

    def acquire(self, timeout: int | None = None) -> bool:
        """
        Acquire the exclusive file lock.

        Args:
            timeout: Maximum time to wait for lock in seconds (uses instance timeout if None)

        Returns:
            True if lock acquired, False if timeout or error
        """
        if self._acquired:
            logger.warning(f"Lock already acquired: {self.lock_path}")
            return True

        if timeout is None:
            timeout = self.timeout

        # Ensure parent directory exists
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Clean up stale lock before attempting to acquire
        self._cleanup_stale_lock()

        try:
            self._fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)

            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    _try_lock(self._fd)
                    self._acquired = True
                    logger.debug(f"Acquired lock: {self.lock_path}")
                    return True
                except OSError:
                    # Lock is held by another process
                    if time.time() - start_time >= timeout:
                        logger.warning(f"Timeout waiting for lock: {self.lock_path}")
                        os.close(self._fd)
                        self._fd = None
                        return False

                    time.sleep(self.poll_interval)

            # Timeout reached
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            return False

        except Exception as e:
            logger.error(f"Error acquiring lock: {e}")
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
            return False

    def release(self) -> None:
        """
        Release the file lock.

        This closes the file descriptor and removes the lock file.
        """
        if not self._acquired:
            return

        try:
            # Close file descriptor and release the lock
            if self._fd is not None:
                try:
                    _unlock(self._fd)
                    os.close(self._fd)
                except Exception as e:
                    logger.warning(f"Error closing lock file descriptor: {e}")
                finally:
                    self._fd = None

            # Remove lock file
            if self.lock_path.exists():
                self.lock_path.unlink()
                logger.debug(f"Released lock: {self.lock_path}")

        except FileNotFoundError:
            # Lock file already removed, that's fine
            pass
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")
        finally:
            self._acquired = False

    def __enter__(self):
        """Context manager entry - acquire the lock."""
        if not self.acquire():
            raise TimeoutError(f"Failed to acquire lock: {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release the lock."""
        self.release()
        return False

    def __del__(self):
        """Destructor - ensure lock is released."""
        if self._acquired:
            self.release()


### Retrying file operations


#: Windows error codes another process holding a file produces: access denied,
#: sharing violation and lock violation. Antivirus real time scanning and the
#: search indexer open every file Frigate writes for a moment after it appears,
#: so a move or a delete can fail even though nothing in Frigate holds it.
WINDOWS_SHARING_ERRORS = (5, 32, 33)

FILE_OPERATION_ATTEMPTS = 5
FILE_OPERATION_DELAY = 0.2


def is_held_by_another_process(err: OSError) -> bool:
    """Whether a file operation failed because another process held the file."""
    if getattr(err, "winerror", None) in WINDOWS_SHARING_ERRORS:
        return True

    if IS_WINDOWS:
        # CPython maps a sharing violation onto EACCES as well as winerror
        return err.errno in (errno.EACCES, errno.EBUSY)

    return err.errno == errno.EBUSY


def _run_with_retry(operation, attempts: int, delay: float, description: str) -> None:
    """Run a file operation, retrying while another process holds the file."""
    for attempt in range(1, attempts + 1):
        try:
            operation()
            return
        except OSError as err:
            if attempt == attempts or not is_held_by_another_process(err):
                raise

            logger.debug(
                "Retrying %s, another process holds the file (attempt %d of %d): %s",
                description,
                attempt,
                attempts,
                err,
            )
            time.sleep(delay * attempt)


def robust_remove(
    path: str,
    attempts: int = FILE_OPERATION_ATTEMPTS,
    delay: float = FILE_OPERATION_DELAY,
) -> None:
    """Remove a file, retrying while another process holds it open."""
    _run_with_retry(lambda: os.remove(path), attempts, delay, f"removal of {path}")


def robust_move(
    src: str,
    dst: str,
    attempts: int = FILE_OPERATION_ATTEMPTS,
    delay: float = FILE_OPERATION_DELAY,
) -> None:
    """Move a file or directory, retrying while another process holds it open."""
    _run_with_retry(
        lambda: shutil.move(src, dst), attempts, delay, f"move of {src} to {dst}"
    )


async def async_robust_remove(
    path: str,
    attempts: int = FILE_OPERATION_ATTEMPTS,
    delay: float = FILE_OPERATION_DELAY,
) -> None:
    """Remove a file without blocking the event loop while it retries."""
    for attempt in range(1, attempts + 1):
        try:
            os.remove(path)
            return
        except OSError as err:
            if attempt == attempts or not is_held_by_another_process(err):
                raise

            logger.debug(
                "Retrying removal of %s, another process holds the file "
                "(attempt %d of %d): %s",
                path,
                attempt,
                attempts,
                err,
            )
            await asyncio.sleep(delay * attempt)


async def async_robust_move(
    src: str,
    dst: str,
    attempts: int = FILE_OPERATION_ATTEMPTS,
    delay: float = FILE_OPERATION_DELAY,
) -> None:
    """Move a file or directory without blocking the event loop while it retries."""
    for attempt in range(1, attempts + 1):
        try:
            shutil.move(src, dst)
            return
        except OSError as err:
            if attempt == attempts or not is_held_by_another_process(err):
                raise

            logger.debug(
                "Retrying move of %s to %s, another process holds the file "
                "(attempt %d of %d): %s",
                src,
                dst,
                attempt,
                attempts,
                err,
            )
            await asyncio.sleep(delay * attempt)
