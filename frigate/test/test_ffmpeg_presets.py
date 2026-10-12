import unittest
from unittest.mock import patch

from frigate.config import FrigateConfig
from frigate.config.camera.ffmpeg import FFMPEG_INPUT_ARGS_DEFAULT
from frigate.ffmpeg_presets import (
    PRESETS_HW_ACCEL_DECODE,
    PRESETS_INPUT,
    PRESETS_RECORD_OUTPUT,
    EncodeTypeEnum,
    parse_preset_hardware_acceleration_decode,
    parse_preset_hardware_acceleration_encode,
    parse_preset_input,
    validate_ffmpeg_args,
    windows_decode_presets,
)
from frigate.util.platform import IS_WINDOWS, quote_ffmpeg_arg, split_ffmpeg_command


class TestFfmpegPresets(unittest.TestCase):
    def setUp(self):
        self.default_ffmpeg = {
            "mqtt": {"host": "mqtt"},
            "cameras": {
                "back": {
                    "ffmpeg": {
                        "inputs": [
                            {
                                "path": "rtsp://10.0.0.1:554/video",
                                "roles": ["detect"],
                            }
                        ],
                        "output_args": {
                            "detect": "-f rawvideo -pix_fmt yuv420p",
                            "record": "-f segment -segment_time 10 -segment_format mp4 -reset_timestamps 1 -strftime 1 -c copy -an",
                        },
                    },
                    "detect": {
                        "height": 1080,
                        "width": 1920,
                        "fps": 5,
                    },
                    "record": {
                        "enabled": True,
                    },
                    "name": "back",
                }
            },
        }

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

        self.assertEqual(
            split_ffmpeg_command(command)[0],
            executable,
        )
        self.assertEqual(split_ffmpeg_command(command)[-1], output)

    def test_default_ffmpeg(self):
        FrigateConfig(**self.default_ffmpeg)

    @unittest.skipIf(
        IS_WINDOWS, "preset-rpi-64-h264 decodes through the Raspberry Pi v4l2m2m API"
    )
    def test_ffmpeg_hwaccel_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["hwaccel_args"] = (
            "preset-rpi-64-h264"
        )
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "preset-rpi-64-h264" not in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )
        assert "-c:v:1 h264_v4l2m2m" in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )

    @unittest.skipIf(
        IS_WINDOWS, "preset-apple-silicon-h265 needs the Apple media engine"
    )
    def test_ffmpeg_hwaccel_apple_preset_decodes_every_stream(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["hwaccel_args"] = (
            "preset-apple-silicon-h265"
        )
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        cmd = " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        assert "-c:v hevc_v4l2m2m" in cmd
        assert "-c:v:1" not in cmd
        assert "scale=1920:1080" in cmd

    def test_ffmpeg_hwaccel_not_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["hwaccel_args"] = (
            "-other-hwaccel args"
        )
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "-other-hwaccel args" in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )

    @unittest.skipIf(
        IS_WINDOWS, "preset-nvidia-h264 carries a Linux DRM device argument"
    )
    def test_ffmpeg_hwaccel_scale_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["hwaccel_args"] = (
            "preset-nvidia-h264"
        )
        self.default_ffmpeg["cameras"]["back"]["detect"] = {
            "height": 1920,
            "width": 2560,
            "fps": 10,
        }
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "preset-nvidia-h264" not in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )
        assert "fps=10,scale_cuda=w=2560:h=1920,hwdownload,format=nv12" in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )

    def test_default_ffmpeg_input_arg_preset(self):
        frigate_config = FrigateConfig(**self.default_ffmpeg)

        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["input_args"] = (
            "preset-rtsp-generic"
        )
        frigate_preset_config = FrigateConfig(**self.default_ffmpeg)
        assert (
            # Ignore global and user_agent args in comparison
            frigate_preset_config.cameras["back"].ffmpeg_cmds[0]["cmd"]
            == frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"]
        )

    def test_ffmpeg_input_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["input_args"] = (
            "preset-rtmp-generic"
        )
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "preset-rtmp-generic" not in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )
        assert (" ".join(parse_preset_input("preset-rtmp-generic", 5))) in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )

    def test_ffmpeg_input_args_as_string(self):
        # Strip user_agent args here to avoid handling quoting issues
        defaultArgsList = parse_preset_input(FFMPEG_INPUT_ARGS_DEFAULT, 5)[2::]
        argsString = " ".join(defaultArgsList) + ' -some "arg with space"'
        argsList = defaultArgsList + ["-some", "arg with space"]
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["input_args"] = argsString
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert set(argsList).issubset(
            frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"]
        )

    def test_ffmpeg_input_not_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["input_args"] = "-some inputs"
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "-some inputs" in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )

    def test_ffmpeg_output_record_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["output_args"]["record"] = (
            "preset-record-generic-audio-aac"
        )
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "preset-record-generic-audio-aac" not in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )
        assert "-c:v copy -c:a aac" in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )

    def test_ffmpeg_output_record_not_preset(self):
        self.default_ffmpeg["cameras"]["back"]["ffmpeg"]["output_args"]["record"] = (
            "-some output -segment_time 10"
        )
        frigate_config = FrigateConfig(**self.default_ffmpeg)
        assert "-some output" in (
            " ".join(frigate_config.cameras["back"].ffmpeg_cmds[0]["cmd"])
        )


class TestPresetValidation(unittest.TestCase):
    """An unusable preset has to be a config error, not a broken ffmpeg run.

    A value starting with ``preset-`` that is not in the relevant map used to
    be passed to ffmpeg untouched, so a typo or a Linux only preset produced a
    camera that never started with an error pointing nowhere near the config.
    """

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

    def test_raw_arguments_are_not_validated_as_presets(self):
        validate_ffmpeg_args(
            "-hwaccel cuda -hwaccel_output_format cuda",
            "ffmpeg.hwaccel_args",
            PRESETS_HW_ACCEL_DECODE,
        )
        validate_ffmpeg_args(
            ["-threads", "2"], "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
        )

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(ValueError) as raised:
            validate_ffmpeg_args(
                "preset-windows-not-a-thing",
                "ffmpeg.hwaccel_args",
                PRESETS_HW_ACCEL_DECODE,
            )

        self.assertIn("not a known preset", str(raised.exception))

    def test_windows_preset_is_listed_for_windows(self):
        with patch("frigate.ffmpeg_presets.IS_WINDOWS", True):
            with self.assertRaises(ValueError) as raised:
                validate_ffmpeg_args(
                    "preset-bogus", "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                )

        self.assertIn("preset-windows-d3d11va", str(raised.exception))
        self.assertNotIn("preset-rkmpp", str(raised.exception))

    def test_linux_only_presets_are_rejected_on_windows(self):
        cases = {
            "preset-vaapi": "preset-windows-d3d11va",
            "preset-intel-qsv-h264": "preset-windows-qsv-h264",
            "preset-intel-qsv-h265": "preset-windows-qsv-h265",
            "preset-nvidia": "preset-windows-nvenc",
            "preset-nvidia-h264": "preset-windows-nvenc",
        }

        with patch("frigate.ffmpeg_presets.IS_WINDOWS", True):
            for preset, replacement in cases.items():
                with self.subTest(preset=preset):
                    with self.assertRaises(ValueError) as raised:
                        validate_ffmpeg_args(
                            preset, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                        )

                    self.assertIn(replacement, str(raised.exception))

    @unittest.skipIf(
        IS_WINDOWS, "the config validator rejects Linux decode presets on Windows"
    )
    def test_linux_only_presets_still_work_on_linux(self):
        for preset in ("preset-vaapi", "preset-nvidia", "preset-intel-qsv-h264"):
            with self.subTest(preset=preset):
                validate_ffmpeg_args(
                    preset, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                )

    def test_other_platform_presets_are_rejected_on_windows(self):
        with patch("frigate.ffmpeg_presets.IS_WINDOWS", True):
            for preset in ("preset-rpi-64-h264", "preset-jetson-h265"):
                with self.subTest(preset=preset):
                    with self.assertRaises(ValueError):
                        validate_ffmpeg_args(
                            preset, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                        )

    def test_cross_platform_presets_are_allowed_on_windows(self):
        with patch("frigate.ffmpeg_presets.IS_WINDOWS", True):
            for preset in ("preset-amd-amf", "preset-vulkan", "preset-windows-dxva2"):
                with self.subTest(preset=preset):
                    validate_ffmpeg_args(
                        preset, "ffmpeg.hwaccel_args", PRESETS_HW_ACCEL_DECODE
                    )


class TestWindowsDecodePresets(unittest.TestCase):
    def test_documented_windows_presets_exist_for_decode(self):
        # the installation docs tell users to set each of these
        for preset in (
            "preset-windows-d3d11va",
            "preset-windows-dxva2",
            "preset-windows-qsv-h264",
            "preset-windows-qsv-h265",
            "preset-windows-nvenc",
        ):
            with self.subTest(preset=preset):
                self.assertIn(preset, PRESETS_HW_ACCEL_DECODE)

    def test_windows_presets_never_reference_a_linux_device(self):
        for preset in windows_decode_presets():
            args = parse_preset_hardware_acceleration_decode(preset, 5, 1920, 1080, 0)

            self.assertIsNotNone(args)
            self.assertNotIn("qsv_device", args)
            self.assertNotIn("-hwaccel_device", args)
            self.assertNotIn("/dev/dri", args)
            self.assertNotIn("vaapi", " ".join(args))

    def test_nvenc_preset_decodes_through_cuda(self):
        args = parse_preset_hardware_acceleration_decode(
            "preset-windows-nvenc", 5, 1920, 1080, 0
        )

        self.assertEqual(args, ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
