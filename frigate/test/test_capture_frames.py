"""Tests for reading decoded frames out of ffmpeg's stdout.

The frame reader asks a pipe for one frame at a time and writes it into a
shared memory segment. What makes that tricky is the buffer: on POSIX it is
exactly one frame long, because that is the size the segment was created
with, but on Windows a segment is mapped page aligned, so the buffer handed
back is a few kilobytes longer than the frame.

``readinto`` fills the whole buffer it is given, so a reader that hands it
the segment verbatim takes the start of the *next* frame along with the
current one. Every frame after that is offset by the difference, fails its
length check, and the capture thread logs it and drops it — recording
segments keep being written to the cache while no frame ever reaches
detection, which is what the end to end test found.
"""

import queue
import threading
import unittest
from multiprocessing import Event as MpEvent
from multiprocessing import Value
from unittest.mock import MagicMock

import numpy as np

from frigate.video.ffmpeg import capture_frames

#: 320x240 in yuv420p, the shape the end to end camera uses
FRAME_SIZE = 320 * 240 * 3 // 2
FRAME_SHAPE = (360, 320)

#: how much longer a page aligned segment is than the frame in it
SLACK = 4096


class _Pipe:
    """stdout of an ffmpeg process, filled the way a decoded stream is.

    ``readinto`` fills the buffer it is handed, up to the bytes left behind
    it, exactly as reading a pipe does.
    """

    def __init__(self, stream: bytes) -> None:
        self._stream = bytearray(stream)
        self.requested: list[int] = []

    @property
    def stdout(self):
        """This object stands in for the process as well as its stdout."""
        return self

    def readinto(self, buffer) -> int:
        self.requested.append(len(buffer))
        taken = min(len(buffer), len(self._stream))
        buffer[:taken] = self._stream[:taken]
        del self._stream[:taken]
        return taken

    def poll(self):
        return None


class _FrameManager:
    """Shared memory whose segments are longer than the frames in them."""

    #: the capture thread is handed this many ring slots
    RING_SLOTS = 10

    def __init__(self, frame_size: int, slack: int) -> None:
        self._frame_size = frame_size
        self._slack = slack
        self.segments: dict[str, bytearray] = {}
        self.closed: list[str] = []

    def write(self, name: str):
        segment = self.segments.setdefault(
            name, bytearray(self._frame_size + self._slack)
        )
        return memoryview(segment)

    def frame_times(self, camera: str) -> np.ndarray:
        """The per-slot write ledger, backed the way the real manager backs it.

        ``camera/maintainer`` creates this segment at one float64 per ring
        slot, so unlike a frame slot it is not frame sized and carries no page
        slack. Capture zeroes a slot before it waits on the pipe and stamps it
        once the frame is whole, which is what tells a reader a lapped slot
        from a frame that is still being read.
        """
        segment = self.segments.setdefault(
            f"{camera}_frame_times", bytearray(self.RING_SLOTS * 8)
        )
        return np.ndarray((len(segment) // 8,), dtype=np.float64, buffer=segment)

    def close(self, name: str) -> None:
        self.closed.append(name)

    def frame(self, name: str) -> bytes:
        return bytes(self.segments[name][: self._frame_size])


class TestCaptureFrames(unittest.TestCase):
    def _capture(
        self, frames: int = 3
    ) -> tuple[list[tuple[str, float]], _Pipe, _FrameManager]:
        stream = b"".join(bytes([index]) * FRAME_SIZE for index in range(frames + 1))
        pipe = _Pipe(stream)
        frame_manager = _FrameManager(FRAME_SIZE, SLACK)
        frame_queue: queue.Queue = queue.Queue()

        config = MagicMock()
        config.name = "test_cam"
        config.enabled = True

        stop_event = MpEvent()
        error: list[BaseException] = []

        def run() -> None:
            try:
                capture_frames(
                    pipe,
                    config,
                    10,
                    0,
                    FRAME_SHAPE,
                    frame_manager,
                    frame_queue,
                    Value("d", 0.0),
                    Value("d", 0.0),
                    Value("d", 0.0),
                    stop_event,
                )
            except BaseException as err:  # pragma: no cover - reported below
                error.append(err)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        captured = []
        try:
            for _ in range(frames):
                captured.append(frame_queue.get(timeout=10))
        except queue.Empty:
            stop_event.set()
            thread.join(timeout=10)
            if error:  # pragma: no cover - a bug in the thread, not the test
                raise error[0] from None
            self.fail(
                "the capture thread did not produce "
                f"{frames} frames from {frames * FRAME_SIZE} bytes of stream"
            )
        finally:
            stop_event.set()
            thread.join(timeout=10)

        if error:  # pragma: no cover - a bug in the thread, not the test
            raise error[0] from None

        return captured, pipe, frame_manager

    def test_reads_exactly_one_frame_into_a_longer_buffer(self) -> None:
        captured, pipe, frame_manager = self._capture(frames=3)

        # every read asked for one frame, not for the segment's mapped length
        self.assertEqual(set(pipe.requested), {FRAME_SIZE})

        for index, (frame_name, _) in enumerate(captured):
            self.assertEqual(
                frame_manager.frame(frame_name),
                bytes([index]) * FRAME_SIZE,
                f"frame {index} did not land in {frame_name} intact",
            )

    def test_a_short_read_is_reported_rather_than_desynchronizing(self) -> None:
        """A pipe that ends mid frame must not leave a partial frame behind."""
        stream = bytes([7]) * (FRAME_SIZE - 1)
        pipe = _Pipe(stream)
        frame_manager = _FrameManager(FRAME_SIZE, SLACK)
        frame_queue: queue.Queue = queue.Queue()

        config = MagicMock()
        config.name = "test_cam"
        config.enabled = True

        stop_event = MpEvent()
        thread = threading.Thread(
            target=capture_frames,
            args=(
                pipe,
                config,
                10,
                0,
                FRAME_SHAPE,
                frame_manager,
                frame_queue,
                Value("d", 0.0),
                Value("d", 0.0),
                Value("d", 0.0),
                stop_event,
            ),
            daemon=True,
        )
        thread.start()

        # the reader complains once per attempt, which is the point of the
        # test rather than noise for whoever reads the test output
        with self.assertLogs("frigate.video.ffmpeg", level="ERROR"):
            with self.assertRaises(queue.Empty):
                frame_queue.get(timeout=1)

        stop_event.set()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "the capture thread did not stop")


if __name__ == "__main__":
    unittest.main()
