"""Tests for native Windows FFmpeg executable resolution."""

import os
import unittest
from unittest.mock import patch

from frigate.util.config import resolve_ffmpeg_path

CUSTOM_DIR = r"C:\Frigate\tools"
CUSTOM_EXE = os.path.join(CUSTOM_DIR, "ffmpeg.exe")
CUSTOM_BIN_EXE = os.path.join(CUSTOM_DIR, "bin", "ffmpeg.exe")


class TestFfmpegPathResolution(unittest.TestCase):
    def test_explicit_executable_is_used(self) -> None:
        with patch("frigate.util.config.os.path.isfile", return_value=True):
            self.assertEqual(resolve_ffmpeg_path(CUSTOM_EXE), CUSTOM_EXE)

    def test_direct_executable_in_directory_is_preferred(self) -> None:
        def is_file(path: str) -> bool:
            return path == CUSTOM_EXE

        with (
            patch("frigate.util.config.os.path.isfile", side_effect=is_file),
            patch("frigate.util.config.find_executable", return_value=None),
        ):
            self.assertEqual(resolve_ffmpeg_path(CUSTOM_DIR), CUSTOM_EXE)

    def test_bin_subdirectory_is_supported(self) -> None:
        def is_file(path: str) -> bool:
            return path == CUSTOM_BIN_EXE

        with (
            patch("frigate.util.config.os.path.isfile", side_effect=is_file),
            patch("frigate.util.config.find_executable", return_value=None),
        ):
            self.assertEqual(resolve_ffmpeg_path(CUSTOM_DIR), CUSTOM_BIN_EXE)

    def test_path_lookup_precedes_bootstrap_location_for_default(self) -> None:
        discovered = r"C:\Program Files\FFmpeg\bin\ffmpeg.exe"
        with patch("frigate.util.config.find_executable", return_value=discovered):
            self.assertEqual(resolve_ffmpeg_path("default"), discovered)

    def test_ffprobe_uses_exe_suffix(self) -> None:
        expected = os.path.join(CUSTOM_DIR, "bin", "ffprobe.exe")
        with (
            patch("frigate.util.config.os.path.isfile", return_value=False),
            patch("frigate.util.config.find_executable", return_value=None),
        ):
            self.assertEqual(resolve_ffmpeg_path(CUSTOM_DIR, "ffprobe"), expected)


if __name__ == "__main__":
    unittest.main()
