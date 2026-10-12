"""Platform helper tests for Windows command and path handling."""

import unittest
from unittest.mock import MagicMock, patch

from frigate.util.platform import (
    ffmpeg_concat_file_line,
    ffmpeg_stdin_input,
    get_process_priority_class,
    quote_ffmpeg_arg,
    split_ffmpeg_command,
)


class TestWindowsFfmpegPaths(unittest.TestCase):
    def test_stdin_uses_pipe_protocol_on_windows(self):
        with patch("frigate.util.platform.IS_WINDOWS", True):
            self.assertEqual(ffmpeg_stdin_input(), "pipe:0")

    def test_concat_file_line_normalizes_windows_paths_and_quotes(self):
        path = "C:\\Program Files\\Cam's Clips\\front.mp4"
        with patch("frigate.util.platform.IS_WINDOWS", True):
            line = ffmpeg_concat_file_line(path)

        self.assertEqual(line, "file 'C:/Program Files/Cam'\\''s Clips/front.mp4'")

    def test_command_parser_preserves_paths_with_spaces(self):
        executable = r"C:\Program Files\FFmpeg\bin\ffmpeg.exe"
        media_path = r"C:\Frigate Data\recordings\front.mp4"
        with patch("frigate.util.platform.IS_WINDOWS", True):
            command = (
                f"{quote_ffmpeg_arg(executable)} -i "
                f"{quote_ffmpeg_arg(media_path)} output.mp4"
            )
            parsed = split_ffmpeg_command(command)

        self.assertEqual(parsed, [executable, "-i", media_path, "output.mp4"])

    def test_low_priority_maps_to_windows_idle_class(self):
        psutil = MagicMock()
        psutil.IDLE_PRIORITY_CLASS = 0x40
        psutil.BELOW_NORMAL_PRIORITY_CLASS = 0x4000
        psutil.NORMAL_PRIORITY_CLASS = 0x20
        with (
            patch("frigate.util.platform.IS_WINDOWS", True),
            patch.dict("sys.modules", {"psutil": psutil}),
        ):
            self.assertEqual(get_process_priority_class(19), 0x40)
            self.assertEqual(get_process_priority_class(5), 0x4000)
            self.assertEqual(get_process_priority_class(0), 0x20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
