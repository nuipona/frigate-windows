"""Native Windows go2rtc configuration and HomeKit persistence tests."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frigate.util.go2rtc_config import (
    HOMEKIT_CONFIG_MAX_BYTES,
    build_go2rtc_config,
    prepare_homekit_config,
)
from frigate.util.go2rtc_process import Go2rtcProcess


class TestHomeKitConfig(unittest.TestCase):
    def test_creates_an_empty_writable_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "go2rtc_homekit.yml"

            self.assertEqual(prepare_homekit_config(str(path)), str(path))
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_preserves_only_homekit_pairing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "go2rtc_homekit.yml"
            path.write_text(
                "homekit:\n  front:\n    pin: '12345678'\nstreams:\n  leaked: rtsp://secret\n",
                encoding="utf-8",
            )

            prepare_homekit_config(str(path))

            content = path.read_text(encoding="utf-8")
            self.assertIn("homekit:", content)
            self.assertIn("front:", content)
            self.assertNotIn("streams:", content)
            self.assertNotIn("secret", content)

    def test_rejects_invalid_or_oversized_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "go2rtc_homekit.yml"
            path.write_text("homekit: [", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid YAML"):
                prepare_homekit_config(str(path))

            path.write_bytes(b"x" * (HOMEKIT_CONFIG_MAX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "too large"):
                prepare_homekit_config(str(path))

    def test_rejects_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "go2rtc_homekit.yml"
            path.mkdir()
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                prepare_homekit_config(str(path))


class TestNativeGo2rtcConfig(unittest.TestCase):
    @patch("frigate.util.go2rtc_config.resolve_ffmpeg_path", return_value="ffmpeg.exe")
    def test_management_api_defaults_to_loopback(self, _resolve: MagicMock) -> None:
        config = build_go2rtc_config({})

        self.assertEqual(config["api"]["listen"], "127.0.0.1:1984")
        self.assertEqual(config["api"]["origin"], "*")

    @patch("frigate.util.go2rtc_config.resolve_ffmpeg_path", return_value="ffmpeg.exe")
    def test_explicit_management_listener_is_preserved(
        self, _resolve: MagicMock
    ) -> None:
        config = build_go2rtc_config(
            {"go2rtc": {"api": {"listen": "127.0.0.1:2984", "origin": "null"}}}
        )

        self.assertEqual(config["api"]["listen"], "127.0.0.1:2984")
        self.assertEqual(config["api"]["origin"], "null")


class TestGo2rtcProcess(unittest.TestCase):
    @patch("frigate.util.go2rtc_process.sp.Popen")
    @patch("frigate.util.go2rtc_process.prepare_homekit_config")
    @patch(
        "frigate.util.go2rtc_process.find_go2rtc",
        return_value=r"C:\\Frigate\\go2rtc.exe",
    )
    @patch("frigate.util.go2rtc_process.get_service_log_path")
    @patch("frigate.util.go2rtc_process.os.makedirs")
    def test_homekit_config_is_the_writable_primary_config(
        self,
        _makedirs: MagicMock,
        service_log_path: MagicMock,
        _find: MagicMock,
        prepare: MagicMock,
        popen: MagicMock,
    ) -> None:
        service_log_path.return_value = os.devnull
        prepare.return_value = r"C:\\ProgramData\\Frigate\\config\\go2rtc_homekit.yml"
        popen.return_value = SimpleNamespace(pid=42)
        process = Go2rtcProcess(r"C:\\ProgramData\\Frigate\\tmp\\go2rtc.yaml")

        with patch(
            "frigate.util.go2rtc_process.sp.CREATE_NEW_PROCESS_GROUP",
            512,
            create=True,
        ):
            self.assertTrue(process.start())

        command = popen.call_args.args[0]
        self.assertEqual(
            command,
            [
                r"C:\\Frigate\\go2rtc.exe",
                "-config",
                r"C:\\ProgramData\\Frigate\\config\\go2rtc_homekit.yml",
                "-config",
                r"C:\\ProgramData\\Frigate\\tmp\\go2rtc.yaml",
            ],
        )
        self.assertIn("creationflags", popen.call_args.kwargs)
        process.log_file.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
