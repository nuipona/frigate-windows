"""Platform helper tests for Windows command and path handling."""

import unittest
from unittest.mock import MagicMock, patch

from frigate.util.builtin import get_ffmpeg_arg_list
from frigate.util.platform import (
    MAX_PATH_LIMIT,
    ffmpeg_concat_file_line,
    ffmpeg_stdin_input,
    get_process_priority_class,
    long_path_warning,
    longest_recording_path,
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

    def test_unquoted_windows_path_keeps_its_separators(self):
        """POSIX escape handling used to eat the backslashes in a path."""
        with patch("frigate.util.platform.IS_WINDOWS", True):
            parsed = split_ffmpeg_command(r"-i D:\videos\cam.mp4")

        self.assertEqual(parsed, ["-i", r"D:\videos\cam.mp4"])

    def test_a_path_with_spaces_has_to_be_quoted(self):
        """Frigate quotes what it builds itself; a hand written config must too."""
        path = r"D:\Frigate Data\clips\front.mp4"

        with patch("frigate.util.platform.IS_WINDOWS", True):
            unquoted = split_ffmpeg_command(f"-i {path}")
            quoted = split_ffmpeg_command(f'-i "{path}"')

        self.assertEqual(unquoted, ["-i", r"D:\Frigate", r"Data\clips\front.mp4"])
        self.assertEqual(quoted, ["-i", path])

    def test_quotes_are_removed_in_windows_mode(self):
        # the C runtime strips the quotes before ffmpeg sees the argument
        with patch("frigate.util.platform.IS_WINDOWS", True):
            parsed = split_ffmpeg_command(r'-vf "scale=1280:720" -i "D:/cams/s1"')

        self.assertEqual(parsed, ["-vf", "scale=1280:720", "-i", "D:/cams/s1"])

    def test_quotes_are_removed_and_escapes_are_applied(self):
        with patch("frigate.util.platform.IS_WINDOWS", True):
            parsed = split_ffmpeg_command(r'-metadata "say \"hi\"" out.mp4')

        self.assertEqual(parsed, ["-metadata", 'say "hi"', "out.mp4"])

    def test_quoted_windows_path_keeps_its_separators(self):
        path = "C:\\Program Files\\cams\\front.mp4"
        with patch("frigate.util.platform.IS_WINDOWS", True):
            parsed = split_ffmpeg_command(f'-i "{path}"')

        self.assertEqual(parsed, ["-i", path])

    def test_get_ffmpeg_arg_list_uses_the_platform_rules(self):
        with patch("frigate.util.platform.IS_WINDOWS", True):
            args = get_ffmpeg_arg_list(r"-re -i D:\videos\cam.mp4")

        self.assertEqual(args, ["-re", "-i", r"D:\videos\cam.mp4"])

    def test_get_ffmpeg_arg_list_leaves_lists_alone(self):
        args = ["-re", "-i", r"D:\videos\cam.mp4"]
        self.assertIs(get_ffmpeg_arg_list(args), args)

    def test_quoting_round_trips_through_the_splitter(self):
        cases = [
            r"D:\videos\cam.mp4",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"D:\Frigate Data\clips\front door.mp4",
        ]

        with patch("frigate.util.platform.IS_WINDOWS", True):
            for value in cases:
                with self.subTest(value=value):
                    command = f"-hide_banner -i {quote_ffmpeg_arg(value)}"
                    self.assertEqual(split_ffmpeg_command(command)[2], value)

    def test_posix_quoting_is_unchanged(self):
        with patch("frigate.util.platform.IS_WINDOWS", False):
            self.assertEqual(
                quote_ffmpeg_arg("/media/frigate/a b.mp4"), "'/media/frigate/a b.mp4'"
            )
            self.assertEqual(
                split_ffmpeg_command("-i '/media/frigate/a b.mp4'"),
                ["-i", "/media/frigate/a b.mp4"],
            )

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


class TestWindowsLongPaths(unittest.TestCase):
    def test_longest_path_uses_the_longest_camera_name(self):
        with (
            patch("frigate.util.platform.IS_WINDOWS", True),
            patch(
                "frigate.util.platform.get_media_dir", return_value="D:\\Frigate\\media"
            ),
        ):
            short = longest_recording_path(["driveway", "front_door"])
            long = longest_recording_path(["driveway", "front_door_camera_name"])

        self.assertIn("front_door_camera_name", long)
        self.assertEqual(
            len(long) - len(short), len("front_door_camera_name") - len("front_door")
        )

    def test_warning_names_the_base_dir_and_the_registry_value(self):
        with (
            patch("frigate.util.platform.IS_WINDOWS", True),
            patch("frigate.util.platform.long_paths_enabled", return_value=False),
            patch("frigate.util.platform.get_base_dir", return_value="D:\\Frigate"),
            patch(
                "frigate.util.platform.get_media_dir",
                return_value="D:\\" + "nested\\" * 30 + "Frigate\\media",
            ),
        ):
            warning = long_path_warning(["driveway"])

        self.assertIsNotNone(warning)
        self.assertIn("D:\\Frigate", warning)
        self.assertIn("LongPathsEnabled", warning)

    def test_no_warning_when_long_paths_are_enabled(self):
        with (
            patch("frigate.util.platform.IS_WINDOWS", True),
            patch("frigate.util.platform.long_paths_enabled", return_value=True),
            patch(
                "frigate.util.platform.get_media_dir",
                return_value="D:\\" + "nested\\" * 30 + "Frigate\\media",
            ),
        ):
            self.assertIsNone(long_path_warning(["driveway"]))

    def test_no_warning_for_a_short_path(self):
        with (
            patch("frigate.util.platform.IS_WINDOWS", True),
            patch("frigate.util.platform.long_paths_enabled", return_value=False),
            patch(
                "frigate.util.platform.get_media_dir", return_value="C:\\Frigate\\media"
            ),
        ):
            self.assertIsNone(long_path_warning(["driveway"]))

        self.assertLess(len("C:\\Frigate\\media\\recordings"), MAX_PATH_LIMIT)

    def test_no_warning_off_windows(self):
        with patch("frigate.util.platform.IS_WINDOWS", False):
            self.assertIsNone(long_path_warning(["driveway"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
