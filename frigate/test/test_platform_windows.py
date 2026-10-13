"""Platform helper tests for Windows command and path handling."""

import os
import unittest
from unittest.mock import MagicMock, patch

from frigate.util.builtin import get_ffmpeg_arg_list
from frigate.util.platform import (
    IPC_ENDPOINTS,
    MAX_PATH_LIMIT,
    ffmpeg_concat_file_line,
    ffmpeg_stdin_input,
    get_fs_type,
    get_process_priority_class,
    get_web_port,
    ipc_endpoint,
    long_path_warning,
    longest_recording_path,
    quote_ffmpeg_arg,
    resolve_media_url_path,
    split_ffmpeg_command,
    to_media_url_path,
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


class TestWebPort(unittest.TestCase):
    def test_out_of_range_ports_fall_back_to_the_internal_port(self):
        """A port outside 1-65535 cannot be bound, so it is not a port."""
        for value in ("0", "65536", "99999"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"FRIGATE_WEB_PORT": value}),
            ):
                self.assertEqual(get_web_port(5000), 5000)

    def test_a_valid_port_is_used(self):
        with patch.dict(os.environ, {"FRIGATE_WEB_PORT": "8971"}):
            self.assertEqual(get_web_port(5000), 8971)

    def test_a_non_numeric_port_falls_back(self):
        with patch.dict(os.environ, {"FRIGATE_WEB_PORT": "https"}):
            self.assertEqual(get_web_port(5000), 5000)


class TestIpcEndpoints(unittest.TestCase):
    def test_unknown_names_do_not_collide_with_known_endpoints(self):
        """An unknown endpoint must not share a known endpoint's port.

        A hash of the name used to be able to land on any of the known
        ports (the name ``a`` hashes onto the zmq detector endpoint), and
        on the birdseye listener 20 ports above the base, so two sockets
        would fight over one loopback port.
        """
        with patch("frigate.util.platform.IS_WINDOWS", True):
            known = {ipc_endpoint(name) for name in IPC_ENDPOINTS}
            birdseye = "tcp://127.0.0.1:5620"

            for name in ("a", "foo", "bar", "detector2", "new_socket"):
                with self.subTest(name=name):
                    endpoint = ipc_endpoint(name)
                    self.assertNotIn(endpoint, known)
                    self.assertNotEqual(endpoint, birdseye)

    def test_unknown_names_are_stable(self):
        with patch("frigate.util.platform.IS_WINDOWS", True):
            self.assertEqual(
                ipc_endpoint("future_socket"), ipc_endpoint("future_socket")
            )


class TestMediaUrlPaths(unittest.TestCase):
    def test_cache_urls_resolve_into_the_cache_dir(self):
        """The cache dir is the /cache URL root, not a directory inside it."""
        with (
            patch("frigate.util.platform.get_media_dir", return_value="/media/frigate"),
            patch("frigate.util.platform.get_cache_dir", return_value="/tmp/cache"),
        ):
            self.assertEqual(
                resolve_media_url_path("/cache/previews/front_door/1.jpg"),
                "/tmp/cache/previews/front_door/1.jpg",
            )
            self.assertEqual(
                to_media_url_path("/tmp/cache/previews/front_door/1.jpg"),
                "/cache/previews/front_door/1.jpg",
            )

    def test_media_paths_round_trip(self):
        with (
            patch("frigate.util.platform.get_media_dir", return_value="/media/frigate"),
            patch("frigate.util.platform.get_cache_dir", return_value="/tmp/cache"),
        ):
            path = "/media/frigate/recordings/2024-01-01/10/front_door/00.00.mp4"
            self.assertEqual(
                to_media_url_path(path),
                "/recordings/2024-01-01/10/front_door/00.00.mp4",
            )
            self.assertEqual(resolve_media_url_path(to_media_url_path(path)), path)

    def test_a_camera_named_like_a_component_keeps_its_path(self):
        with (
            patch("frigate.util.platform.get_media_dir", return_value="/media/frigate"),
            patch("frigate.util.platform.get_cache_dir", return_value="/tmp/cache"),
        ):
            self.assertEqual(
                to_media_url_path(
                    "/media/frigate/recordings/2024-01-01/10/clips/00.00.mp4"
                ),
                "/recordings/2024-01-01/10/clips/00.00.mp4",
            )


class TestFsType(unittest.TestCase):
    def test_a_mountpoint_prefix_is_not_a_match(self):
        """``/media2`` is not on the filesystem mounted at ``/media``."""
        partition = MagicMock()
        partition.mountpoint = "/media"
        partition.fstype = "ext4"

        with patch("psutil.disk_partitions", return_value=[partition]):
            self.assertEqual(get_fs_type("/media2/recordings"), "")
            self.assertEqual(get_fs_type("/media/recordings"), "ext4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
