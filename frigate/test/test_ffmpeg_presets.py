"""Tests for the FFmpeg presets supported by native Windows."""

import unittest
from unittest.mock import patch

from frigate.config import FrigateConfig
from frigate.config.camera.ffmpeg import (
    CameraInput,
    FfmpegConfig,
    FfmpegOutputArgsConfig,
)
from frigate.ffmpeg_presets import (
    PRESETS_HW_ACCEL_DECODE,
    PRESETS_INPUT,
    PRESETS_RECORD_OUTPUT,
    EncodeTypeEnum,
    parse_preset_hardware_acceleration_decode,
    parse_preset_hardware_acceleration_encode,
    parse_preset_hardware_acceleration_scale,
    parse_preset_input,
    parse_preset_output_record,
    validate_ffmpeg_args,
    windows_decode_presets,
)
from frigate.util.platform import quote_ffmpeg_arg, split_ffmpeg_command


class TestFfmpegPresets(unittest.TestCase):
    def test_encode_preset_preserves_windows_paths_with_spaces(self):
        executable = r"C:\Program Files\FFmpeg\bin\ffmpeg.exe"
        output = r"C:\Frigate Media\birdseye preview.mp4"
        command = parse_preset_hardware_acceleration_encode(
            executable,
            None,
            "-i pipe:0",
            f"-f mp4 {quote_ffmpeg_arg(output)}",
            EncodeTypeEnum.preview,
        )

        self.assertEqual(split_ffmpeg_command(command)[0], executable)
        self.assertEqual(split_ffmpeg_command(command)[-1], output)

    def test_unknown_encode_preset_falls_back_to_software(self):
        command = parse_preset_hardware_acceleration_encode(
            "ffmpeg.exe", "preset-linux-only", "-i pipe:0", "output.mp4"
        )

        self.assertIn("libx264", command)

    def test_windows_decode_preset_expands(self):
        args = parse_preset_hardware_acceleration_decode(
            "preset-windows-nvenc", 5, 1920, 1080, 0
        )

        self.assertEqual(args, ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])

    def test_raw_decode_arguments_are_not_treated_as_a_preset(self):
        self.assertIsNone(
            parse_preset_hardware_acceleration_decode(
                "-hwaccel d3d11va", 5, 1920, 1080, 0
            )
        )

    def test_matching_gpu_scale_preset_is_used(self):
        args = parse_preset_hardware_acceleration_scale(
            "preset-windows-qsv", ["-f", "rawvideo"], 10, 1280, 720
        )

        self.assertIn("fps=10,vpp_qsv=w=1280:h=720:format=nv12,hwdownload,format=nv12,format=yuv420p", args)
        self.assertEqual(args[-2:], ["-f", "rawvideo"])

    def test_unknown_scale_preset_uses_software(self):
        args = parse_preset_hardware_acceleration_scale(
            "preset-not-supported", [], 5, 640, 360
        )

        self.assertIn("fps=5,scale=640:360", args)

    def test_input_preset_expands(self):
        args = parse_preset_input("preset-rtsp-generic", 5)

        self.assertIn("-rtsp_transport", args)
        self.assertNotIn("preset-rtsp-generic", args)

    def test_http_jpeg_preset_receives_detect_fps(self):
        args = parse_preset_input("preset-http-jpeg-generic", 12)

        self.assertEqual(args[:2], ["-r", "12"])

    def test_record_preset_expands(self):
        args = parse_preset_output_record(
            "preset-record-generic-audio-aac", force_record_hvc1=False
        )

        self.assertIn("-c:a", args)
        self.assertIn("aac", args)

    def test_unknown_input_and_record_presets_do_not_expand(self):
        self.assertIsNone(parse_preset_input("preset-input-nope", 5))
        self.assertIsNone(
            parse_preset_output_record("preset-record-nope", force_record_hvc1=False)
        )

    @patch("frigate.config.config._ensure_default_cpu_model")
    def test_config_builds_command_with_windows_hwaccel(self, _ensure_model):
        config = FrigateConfig(
            mqtt={"host": "mqtt"},
            cameras={
                "back": {
                    "ffmpeg": {
                        "hwaccel_args": "preset-windows-d3d11va",
                        "inputs": [
                            {
                                "path": "rtsp://10.0.0.1:554/video",
                                "roles": ["detect"],
                            }
                        ],
                    },
                    "detect": {"height": 720, "width": 1280, "fps": 5},
                }
            },
        )

        command = config.cameras["back"].ffmpeg_cmds[0]["cmd"]
        self.assertIn("-hwaccel", command)
        self.assertIn("d3d11va", command)


class TestPresetValidation(unittest.TestCase):
    def test_known_presets_are_accepted(self):
        validate_ffmpeg_args(
            "preset-windows-d3d11va", "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
        )
        validate_ffmpeg_args(
            ["preset-rtsp-generic"], "ffmpeg.input_args", PRESETS_INPUT
        )
        validate_ffmpeg_args(
            "preset-record-generic", "ffmpeg.output_args.record", PRESETS_RECORD_OUTPUT
        )

    def test_windows_raw_hwaccel_args_are_allowed(self):
        for args in (
            "-hwaccel d3d11va",
            "-hwaccel_device 0",
            "-hwaccel qsv -c:v h264_qsv",
            "-hwaccel cuda -hwaccel_output_format cuda",
        ):
            with self.subTest(args=args):
                validate_ffmpeg_args(
                    args, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                )

    def test_unknown_preset_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not a supported preset"):
            validate_ffmpeg_args(
                "preset-windows-not-a-thing",
                "ffmpeg.hwaccel_args",
                PRESETS_HW_ACCEL_DECODE,
            )

    def test_non_windows_presets_are_rejected(self):
        for preset in (
            "preset-vaapi",
            "preset-intel-qsv-h264",
            "preset-rkmpp",
            "preset-nvidia-h264",
            "preset-rpi-64-h264",
            "preset-jetson-h265",
        ):
            with self.subTest(preset=preset):
                with self.assertRaises(ValueError):
                    validate_ffmpeg_args(
                        preset, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                    )

    def test_linux_only_raw_hardware_args_are_rejected(self):
        cases = (
            ("-hwaccel vaapi", "vaapi is not available"),
            (["-hwaccel_device", "/dev/dri/renderD128"], "not a Windows hardware device"),
            (["-c:v", "h264_v4l2m2m"], "codec unavailable on Windows"),
            (["-f", "v4l2"], "V4L2 input is not available"),
            (["-pix_fmt", "drm_prime"], "pixel format unavailable on Windows"),
        )

        for args, message in cases:
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, message):
                    validate_ffmpeg_args(
                        args, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                    )

    def test_raw_hardware_args_are_checked_in_config_fields(self):
        with self.assertRaisesRegex(ValueError, "vaapi is not available"):
            FfmpegConfig(global_args="-hwaccel vaapi")

        with self.assertRaisesRegex(ValueError, "codec unavailable on Windows"):
            FfmpegOutputArgsConfig(detect=["-c:v", "h264_vaapi"])

    def test_linux_camera_input_paths_are_rejected(self):
        for path in (
            "/dev/video0",
            "unix:///tmp/camera.sock",
            "v4l2:///dev/video0",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "cannot be used on native Windows"):
                    CameraInput(path=path, roles=["detect"])

    def test_windows_and_network_camera_paths_are_allowed(self):
        CameraInput(path="video=Integrated Camera", roles=["detect"])
        CameraInput(path="rtsp://camera/stream", roles=["detect"])


class TestWindowsDecodePresets(unittest.TestCase):
    def test_documented_windows_presets_exist_for_decode(self):
        for preset in (
            "preset-windows-d3d11va",
            "preset-windows-dxva2",
            "preset-windows-qsv-h264",
            "preset-windows-qsv-h265",
            "preset-windows-nvenc",
            "preset-windows-amf",
            "preset-windows-vulkan",
        ):
            with self.subTest(preset=preset):
                self.assertIn(preset, PRESETS_HW_ACCEL_DECODE)

    def test_windows_presets_never_reference_a_linux_device(self):
        for preset in windows_decode_presets():
            args = parse_preset_hardware_acceleration_decode(preset, 5, 1920, 1080, 0)

            self.assertIsNotNone(args)
            joined = " ".join(args)
            self.assertNotIn("/dev/", joined)
            self.assertNotIn("vaapi", joined)
            self.assertNotIn("v4l2", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
