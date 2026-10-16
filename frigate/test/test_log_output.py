"""Tests for file-descriptor-level log suppression."""

import os
import tempfile
import unittest
from unittest.mock import patch

from frigate.log import suppress_os_output


class TestSuppressOsOutput(unittest.TestCase):
    def test_discards_native_output_without_an_unread_pipe(self) -> None:
        def noisy_native_call() -> str:
            os.write(1, b"native stdout")
            os.write(2, b"native stderr")
            return "done"

        # The former implementation redirected to an unread pipe, which can
        # fill and block when a native library emits enough output. os.devnull
        # is the portable sink on both Windows and POSIX.
        with (
            tempfile.TemporaryFile() as stdout_capture,
            tempfile.TemporaryFile() as stderr_capture,
        ):
            original_stdout_fd = os.dup(1)
            original_stderr_fd = os.dup(2)
            try:
                os.dup2(stdout_capture.fileno(), 1)
                os.dup2(stderr_capture.fileno(), 2)
                with patch(
                    "frigate.log.os.pipe",
                    side_effect=AssertionError("unexpected pipe"),
                ):
                    result = suppress_os_output(noisy_native_call)()
            finally:
                os.dup2(original_stdout_fd, 1)
                os.dup2(original_stderr_fd, 2)
                os.close(original_stdout_fd)
                os.close(original_stderr_fd)

            stdout_capture.seek(0)
            stderr_capture.seek(0)
            self.assertEqual(stdout_capture.read(), b"")
            self.assertEqual(stderr_capture.read(), b"")

        self.assertEqual(result, "done")

    def test_restores_standard_descriptors_when_function_raises(self) -> None:
        def failing_native_call() -> None:
            os.write(1, b"native stdout")
            os.write(2, b"native stderr")
            raise RuntimeError("native call failed")

        original_descriptors = {
            fd: (os.fstat(fd).st_dev, os.fstat(fd).st_ino, os.fstat(fd).st_mode)
            for fd in (1, 2)
        }

        with (
            patch("frigate.log.os.pipe", side_effect=AssertionError("unexpected pipe")),
            self.assertRaisesRegex(RuntimeError, "native call failed"),
        ):
            suppress_os_output(failing_native_call)()

        for fd, original in original_descriptors.items():
            info = os.fstat(fd)
            self.assertEqual((info.st_dev, info.st_ino, info.st_mode), original)


if __name__ == "__main__":
    unittest.main()
