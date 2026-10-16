"""FFmpeg presets supported by the native Windows application."""

import logging
from enum import Enum
from typing import Any

from frigate.const import FFMPEG_HVC1_ARGS, LIBAVFORMAT_VERSION_MAJOR
from frigate.util.platform import quote_ffmpeg_arg, split_ffmpeg_command
from frigate.util.version import VERSION

logger = logging.getLogger(__name__)

FPS_VFR_PARAM = "-fps_mode vfr" if LIBAVFORMAT_VERSION_MAJOR >= 59 else "-vsync 2"
TIMEOUT_PARAM = "-timeout" if LIBAVFORMAT_VERSION_MAJOR >= 59 else "-stimeout"

_user_agent_args = ["-user_agent", f"FFmpeg Frigate/{VERSION}"]

# Detect-stream decoding. Presets that keep hardware frames on the GPU have a
# matching scale filter below; D3D11VA/DXVA2 transfer frames to system memory.
PRESETS_HW_ACCEL_DECODE = {
    "preset-windows-d3d11va": "-hwaccel d3d11va",
    "preset-windows-dxva2": "-hwaccel dxva2",
    "preset-windows-qsv": "-hwaccel qsv -hwaccel_output_format qsv",
    "preset-windows-qsv-h264": "-hwaccel qsv -hwaccel_output_format qsv -c:v h264_qsv",
    "preset-windows-qsv-h265": "-hwaccel qsv -hwaccel_output_format qsv -c:v hevc_qsv",
    "preset-windows-nvenc": "-hwaccel cuda -hwaccel_output_format cuda",
    # AMF encoding uses D3D11 surfaces; D3D11VA is AMD's native decode path.
    "preset-windows-amf": "-hwaccel d3d11va",
    "preset-windows-vulkan": "-hwaccel vulkan -init_hw_device vulkan=gpu:0 -filter_hw_device gpu -hwaccel_output_format vulkan",
}

PRESETS_HW_ACCEL_SCALE = {
    "preset-windows-qsv": "-r {0} -vf fps={0},vpp_qsv=w={1}:h={2}:format=nv12,hwdownload,format=nv12,format=yuv420p",
    "preset-windows-qsv-h264": "-r {0} -vf fps={0},vpp_qsv=w={1}:h={2}:format=nv12,hwdownload,format=nv12,format=yuv420p",
    "preset-windows-qsv-h265": "-r {0} -vf fps={0},vpp_qsv=w={1}:h={2}:format=nv12,hwdownload,format=nv12,format=yuv420p",
    "preset-windows-nvenc": "-r {0} -vf fps={0},scale_cuda=w={1}:h={2},hwdownload,format=nv12",
    "preset-windows-vulkan": "-r {0} -vf fps={0},hwupload,scale_vulkan=w={1}:h={2},hwdownload",
    "default": "-r {0} -vf fps={0},scale={1}:{2}",
}

PRESETS_HW_ACCEL_ENCODE_BIRDSEYE = {
    "preset-windows-nvenc": "{0} -hide_banner {1} -c:v h264_nvenc -g 50 -profile:v high -level:v auto -preset:v p2 -tune:v ll {2}",
    "preset-windows-qsv": "{0} -hide_banner {1} -c:v h264_qsv -g 50 -bf 0 -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-windows-qsv-h264": "{0} -hide_banner {1} -c:v h264_qsv -g 50 -bf 0 -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-windows-qsv-h265": "{0} -hide_banner {1} -c:v h264_qsv -g 50 -bf 0 -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-windows-amf": "{0} -hide_banner {1} -c:v h264_amf -g 50 -profile:v high {2}",
    "default": "{0} -hide_banner {1} -c:v libx264 -g 50 -profile:v high -level:v 4.1 -preset:v superfast -tune:v zerolatency {2}",
}

PRESETS_HW_ACCEL_ENCODE_TIMELAPSE = {
    "preset-windows-nvenc": "{0} -hide_banner -hwaccel cuda -extra_hw_frames 8 {1} -c:v h264_nvenc {2}",
    "preset-windows-qsv": "{0} -hide_banner {1} -c:v h264_qsv -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-windows-qsv-h264": "{0} -hide_banner {1} -c:v h264_qsv -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-windows-qsv-h265": "{0} -hide_banner {1} -c:v hevc_qsv -profile:v main -level:v 4.1 -async_depth:v 1 {2}",
    "preset-windows-amf": "{0} -hide_banner {1} -c:v h264_amf -profile:v high {2}",
    "default": "{0} -hide_banner {1} -c:v libx264 -preset:v ultrafast -tune:v zerolatency {2}",
}

PRESETS_HW_ACCEL_ENCODE_PREVIEW = {
    "default": "{0} -hide_banner {1} -c:v libx264 -profile:v baseline -preset:v ultrafast {2}",
}


def parse_preset_hardware_acceleration_decode(
    arg: Any, fps: int, width: int, height: int, gpu: int
) -> list[str] | None:
    """Expand a Windows decode preset, or return ``None`` for raw arguments."""
    if not isinstance(arg, str):
        return None
    decode = PRESETS_HW_ACCEL_DECODE.get(arg)
    if decode is None:
        return None
    return decode.format(fps, width, height, gpu).split(" ")


def parse_preset_hardware_acceleration_scale(
    arg: Any,
    detect_args: list[str],
    fps: int,
    width: int,
    height: int,
) -> list[str]:
    """Expand the matching Windows scale filter or the CPU fallback."""
    if not isinstance(arg, str) or " " in arg:
        scale = PRESETS_HW_ACCEL_SCALE["default"]
    else:
        scale = PRESETS_HW_ACCEL_SCALE.get(arg, PRESETS_HW_ACCEL_SCALE["default"])
    result = scale.format(fps, width, height).split(" ")
    result.extend(detect_args)
    return result


class EncodeTypeEnum(str, Enum):
    birdseye = "birdseye"
    preview = "preview"
    timelapse = "timelapse"


def parse_preset_hardware_acceleration_encode(
    ffmpeg_path: str,
    arg: Any,
    input: str,
    output: str,
    type: EncodeTypeEnum = EncodeTypeEnum.birdseye,
) -> str:
    """Expand a Windows encode preset, falling back to software encoding."""
    if type == EncodeTypeEnum.birdseye:
        arg_map = PRESETS_HW_ACCEL_ENCODE_BIRDSEYE
    elif type == EncodeTypeEnum.preview:
        arg_map = PRESETS_HW_ACCEL_ENCODE_PREVIEW
    else:
        arg_map = PRESETS_HW_ACCEL_ENCODE_TIMELAPSE

    preset = arg_map.get(arg, arg_map["default"]) if isinstance(arg, str) else arg_map["default"]
    return preset.format(quote_ffmpeg_arg(ffmpeg_path), input, output, 0)


PRESETS_INPUT = {
    "preset-http-jpeg-generic": [
        "-r",
        "{}",
        "-stream_loop",
        "-1",
        "-f",
        "image2",
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-strict",
        "experimental",
        "-fflags",
        "+genpts+discardcorrupt",
        "-use_wallclock_as_timestamps",
        "1",
    ],
    "preset-http-mjpeg-generic": _user_agent_args
    + [
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-strict",
        "experimental",
        "-fflags",
        "+genpts+discardcorrupt",
        "-use_wallclock_as_timestamps",
        "1",
    ],
    "preset-http-reolink": _user_agent_args
    + [
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts+discardcorrupt",
        "-flags",
        "low_delay",
        "-strict",
        "experimental",
        "-analyzeduration",
        "1000M",
        "-probesize",
        "1000M",
        "-rw_timeout",
        "10000000",
    ],
    "preset-rtmp-generic": [
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-strict",
        "experimental",
        "-fflags",
        "+genpts+discardcorrupt",
        "-rw_timeout",
        "10000000",
        "-use_wallclock_as_timestamps",
        "1",
        "-f",
        "live_flv",
    ],
    "preset-rtsp-generic": _user_agent_args
    + [
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts+discardcorrupt",
        "-rtsp_transport",
        "tcp",
        TIMEOUT_PARAM,
        "10000000",
        "-use_wallclock_as_timestamps",
        "1",
    ],
    "preset-rtsp-restream": _user_agent_args
    + [
        "-rtsp_transport",
        "tcp",
        TIMEOUT_PARAM,
        "10000000",
    ],
    "preset-rtsp-restream-low-latency": _user_agent_args
    + [
        "-rtsp_transport",
        "tcp",
        TIMEOUT_PARAM,
        "10000000",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
    ],
    "preset-rtsp-udp": _user_agent_args
    + [
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts+discardcorrupt",
        "-rtsp_transport",
        "udp",
        TIMEOUT_PARAM,
        "10000000",
        "-use_wallclock_as_timestamps",
        "1",
    ],
    "preset-rtsp-blue-iris": _user_agent_args
    + [
        "-user_agent",
        f"FFmpeg Frigate/{VERSION}",
        "-avoid_negative_ts",
        "make_zero",
        "-flags",
        "low_delay",
        "-strict",
        "experimental",
        "-fflags",
        "+genpts+discardcorrupt",
        "-rtsp_transport",
        "tcp",
        TIMEOUT_PARAM,
        "10000000",
        "-use_wallclock_as_timestamps",
        "1",
    ],
}


def parse_preset_input(arg: Any, detect_fps: int) -> list[str] | None:
    """Return the correct preset if in preset format otherwise return None."""
    if not isinstance(arg, str):
        return None

    if arg == "preset-http-jpeg-generic":
        input = PRESETS_INPUT[arg].copy()
        input[1] = str(detect_fps)
        return input

    return PRESETS_INPUT.get(arg, None)


PRESETS_RECORD_OUTPUT = {
    "preset-record-generic": [
        "-f",
        "segment",
        "-segment_time",
        "10",
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-c",
        "copy",
        "-an",
    ],
    "preset-record-generic-audio-aac": [
        "-f",
        "segment",
        "-segment_time",
        "10",
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
    ],
    "preset-record-generic-audio-copy": [
        "-f",
        "segment",
        "-segment_time",
        "10",
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-c",
        "copy",
    ],
    "preset-record-mjpeg": [
        "-f",
        "segment",
        "-segment_time",
        "10",
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-c:v",
        "libx264",
        "-an",
    ],
    "preset-record-jpeg": [
        "-f",
        "segment",
        "-segment_time",
        "10",
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-c:v",
        "libx264",
        "-an",
    ],
    "preset-record-ubiquiti": [
        "-f",
        "segment",
        "-segment_time",
        "10",
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-c:v",
        "copy",
        "-ar",
        "44100",
        "-c:a",
        "aac",
    ],
}


def parse_preset_output_record(arg: Any, force_record_hvc1: bool) -> list[str] | None:
    """Return the correct preset if in preset format otherwise return None."""
    if not isinstance(arg, str):
        return None

    preset = PRESETS_RECORD_OUTPUT.get(arg, None)

    if not preset:
        return None

    if force_record_hvc1:
        # Apple only supports HEVC if it is hvc1 (vs. hev1)
        return preset + FFMPEG_HVC1_ARGS

    return preset


# ---------------------------------------------------------------------------
# Windows preset and raw argument validation
# ---------------------------------------------------------------------------


def windows_decode_presets() -> list[str]:
    """Every supported decode preset, in recommendation order."""
    return [
        "preset-windows-d3d11va",
        "preset-windows-nvenc",
        "preset-windows-qsv",
        "preset-windows-qsv-h264",
        "preset-windows-qsv-h265",
        "preset-windows-dxva2",
        "preset-windows-amf",
        "preset-windows-vulkan",
    ]


def _valid_presets(setting: str, presets: dict[str, Any] | list[str]) -> list[str]:
    if setting == "ffmpeg.hwaccel_args":
        return windows_decode_presets()
    return sorted(presets)


def _preset_error(
    value: str, setting: str, presets: dict[str, Any] | list[str]
) -> str | None:
    if not value.startswith("preset-"):
        return None
    if value in presets:
        return None
    return (
        f"{value} is not a supported preset for {setting}; "
        f"valid presets are {', '.join(_valid_presets(setting, presets))}"
    )


_UNSUPPORTED_HWACCEL = {
    "vaapi": "preset-windows-d3d11va",
    "drm": "preset-windows-d3d11va",
    "drm_prime": "preset-windows-d3d11va",
    "rkmpp": "preset-windows-d3d11va",
    "nvmpi": "preset-windows-d3d11va",
    "v4l2m2m": "preset-windows-d3d11va",
    "videotoolbox": "preset-windows-d3d11va",
    "mmal": "preset-windows-d3d11va",
    "mediacodec": "preset-windows-d3d11va",
}
_UNSUPPORTED_CODEC_MARKERS = (
    "_v4l2m2m",
    "_nvmpi",
    "_rkmpp",
    "_vaapi",
    "_omx",
    "_videotoolbox",
    "_mmal",
    "_mediacodec",
)
_POSIX_DEVICE_PREFIXES = (
    "/dev/dri/",
    "/dev/video",
    "/dev/v4l/",
    "/dev/nvidia",
    "/dev/nvhost",
)


def _windows_raw_hardware_error(args: list[str], setting: str) -> str | None:
    """Explain raw FFmpeg arguments that cannot run natively on Windows."""
    tokens: list[str] = []
    for arg in args:
        try:
            tokens.extend(split_ffmpeg_command(arg))
        except ValueError:
            tokens.extend(arg.split())

    lowered = [token.lower() for token in tokens]
    for index, token in enumerate(lowered):
        input_format = None
        if token == "-f" and index + 1 < len(lowered):
            input_format = lowered[index + 1]
        elif token.startswith("-f="):
            input_format = token.split("=", maxsplit=1)[1]

        if input_format in ("v4l2", "video4linux2"):
            return (
                "V4L2 input is not available on Windows. Use a DirectShow input "
                "or a network stream."
            )

        backend = None
        if token in ("-hwaccel", "-hwaccel_output_format", "-init_hw_device"):
            if index + 1 < len(lowered):
                backend = lowered[index + 1].split("=", maxsplit=1)[0]
        elif token.startswith(("-hwaccel=", "-hwaccel_output_format=")):
            backend = token.split("=", maxsplit=1)[1].split("=", maxsplit=1)[0]

        if backend in _UNSUPPORTED_HWACCEL:
            replacement = _UNSUPPORTED_HWACCEL[backend]
            return (
                f"{backend} is not available on Windows. Use {replacement} or "
                "another Windows decode preset."
            )

        device_path = token
        if token.startswith(("-hwaccel_device=", "-vaapi_device=")):
            device_path = token.split("=", maxsplit=1)[1]
        if token == "-vaapi_device" or device_path.startswith(_POSIX_DEVICE_PREFIXES):
            return (
                f"{tokens[index]} is not a Windows hardware device path. "
                "Use a Windows decode preset instead."
            )

        if "drm_prime" in token:
            return f"{tokens[index]} uses a pixel format unavailable on Windows."
        if any(marker in token for marker in _UNSUPPORTED_CODEC_MARKERS):
            return f"{tokens[index]} uses a codec unavailable on Windows."
        if any(
            marker in token for marker in ("scale_vaapi", "format=vaapi", "scale_rkrga")
        ):
            return f"{tokens[index]} uses a filter unavailable on Windows."

    return None


def validate_ffmpeg_platform_args(value: str | list[str] | None, setting: str) -> None:
    """Reject raw FFmpeg arguments that refer to non-Windows APIs."""
    if value is None:
        return
    args = [value] if isinstance(value, str) else value
    error = _windows_raw_hardware_error(args, setting)
    if error:
        raise ValueError(error)


def validate_ffmpeg_args(
    value: str | list[str] | None, setting: str, presets: dict[str, Any] | list[str]
) -> None:
    """Validate a preset or raw argument list for the native Windows runtime."""
    if value is None:
        return

    args = [value] if isinstance(value, str) else value
    for arg in args:
        error = _preset_error(arg, setting, presets)
        if error:
            raise ValueError(error)

    if setting.startswith("ffmpeg."):
        validate_ffmpeg_platform_args(args, setting)
