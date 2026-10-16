"""Handles inserting and maintaining ffmpeg presets."""

import logging
import os
from enum import Enum
from typing import Any

from frigate.const import (
    FFMPEG_HVC1_ARGS,
    FFMPEG_HWACCEL_AMF,
    FFMPEG_HWACCEL_NVIDIA,
    FFMPEG_HWACCEL_RKMPP,
    FFMPEG_HWACCEL_VAAPI,
    FFMPEG_HWACCEL_VULKAN,
    LIBAVFORMAT_VERSION_MAJOR,
)
from frigate.util.platform import IS_WINDOWS, quote_ffmpeg_arg, split_ffmpeg_command
from frigate.util.services import vainfo_hwaccel
from frigate.util.version import VERSION

logger = logging.getLogger(__name__)


class LibvaGpuSelector:
    "Automatically selects the correct libva GPU."

    _valid_gpus: list[str] | None = None

    def __get_valid_gpus(self) -> None:
        """Get valid libva GPUs."""
        if IS_WINDOWS:
            # there is no /dev/dri and no libva on Windows; accelerated
            # decoding uses d3d11va, dxva2, qsv, amf or cuda instead
            self._valid_gpus = []
            return

        if not os.path.exists("/dev/dri"):
            self._valid_gpus = []
            return

        if self._valid_gpus:
            return

        devices = list(filter(lambda d: d.startswith("render"), os.listdir("/dev/dri")))

        if not devices:
            self._valid_gpus = ["/dev/dri/renderD128"]
            return

        if len(devices) < 2:
            self._valid_gpus = [f"/dev/dri/{devices[0]}"]
            return

        self._valid_gpus = []
        for device in devices:
            check = vainfo_hwaccel(device_name=device)

            logger.debug(f"{device} return vainfo status code: {check.returncode}")

            if check.returncode == 0:
                self._valid_gpus.append(f"/dev/dri/{device}")

    def get_gpu_arg(self, preset: str, gpu: int) -> str:
        if "nvidia" in preset:
            return str(gpu)

        if self._valid_gpus is None:
            self.__get_valid_gpus()

        if not self._valid_gpus:
            return ""

        if gpu < len(self._valid_gpus):
            return self._valid_gpus[gpu]
        else:
            logger.warning(f"Invalid GPU index {gpu}, using first valid GPU")
            return self._valid_gpus[0]


FPS_VFR_PARAM = "-fps_mode vfr" if LIBAVFORMAT_VERSION_MAJOR >= 59 else "-vsync 2"
TIMEOUT_PARAM = "-timeout" if LIBAVFORMAT_VERSION_MAJOR >= 59 else "-stimeout"

_gpu_selector = LibvaGpuSelector()
_user_agent_args = [
    "-user_agent",
    f"FFmpeg Frigate/{VERSION}",
]

# Presets for FFMPEG Stream Decoding (detect role)

PRESETS_HW_ACCEL_DECODE = {
    "preset-rpi-64-h264": "-c:v:1 h264_v4l2m2m",
    "preset-rpi-64-h265": "-c:v:1 hevc_v4l2m2m",
    "preset-apple-silicon-h264": "-c:v h264_v4l2m2m",
    "preset-apple-silicon-h265": "-c:v hevc_v4l2m2m",
    FFMPEG_HWACCEL_VAAPI: "-hwaccel_flags allow_profile_mismatch -hwaccel vaapi -hwaccel_device {3} -hwaccel_output_format vaapi",
    "preset-intel-qsv-h264": f"-hwaccel qsv -qsv_device {{3}} -hwaccel_output_format qsv -c:v h264_qsv{' -bsf:v dump_extra' if LIBAVFORMAT_VERSION_MAJOR >= 61 else ''}",  # https://trac.ffmpeg.org/ticket/9766#comment:17
    "preset-intel-qsv-h265": f"-load_plugin hevc_hw -hwaccel qsv -qsv_device {{3}} -hwaccel_output_format qsv{' -bsf:v dump_extra' if LIBAVFORMAT_VERSION_MAJOR >= 61 else ''}",  # https://trac.ffmpeg.org/ticket/9766#comment:17
    FFMPEG_HWACCEL_NVIDIA: "-hwaccel_device {3} -hwaccel cuda -hwaccel_output_format cuda",
    "preset-jetson-h264": "-c:v h264_nvmpi -resize {1}x{2}",
    "preset-jetson-h265": "-c:v hevc_nvmpi -resize {1}x{2}",
    f"{FFMPEG_HWACCEL_RKMPP}-no-dump_extra": "-hwaccel rkmpp -hwaccel_output_format drm_prime",
    # experimental presets
    FFMPEG_HWACCEL_VULKAN: "-hwaccel vulkan -init_hw_device vulkan=gpu:0 -filter_hw_device gpu -hwaccel_output_format vulkan",
    FFMPEG_HWACCEL_AMF: "-hwaccel amf -init_hw_device amf=gpu:0 -filter_hw_device gpu -hwaccel_output_format amf",
}
# Windows uses the platform decode APIs rather than libva. Frames are left in
# system memory so the existing CPU scale presets keep working, which costs one
# copy per frame but avoids a per GPU scale preset for every driver.
#
# ``preset-windows-qsv`` is codec agnostic: bare ``-hwaccel qsv`` lets ffmpeg
# pick the matching qsv decoder for the input codec, which is what
# ``hwaccel_args: auto`` needs. ``preset-windows-nvenc`` matches the name the
# encoder maps use, so one NVIDIA preset works for decode and encode alike.
PRESETS_HW_ACCEL_DECODE.update(
    {
        "preset-windows-d3d11va": "-hwaccel d3d11va",
        "preset-windows-dxva2": "-hwaccel dxva2",
        "preset-windows-qsv": "-hwaccel qsv",
        "preset-windows-qsv-h264": "-hwaccel qsv -c:v h264_qsv",
        "preset-windows-qsv-h265": "-load_plugin hevc_hw -hwaccel qsv -c:v hevc_qsv",
        "preset-windows-nvenc": "-hwaccel cuda -hwaccel_output_format cuda",
    }
)

PRESETS_HW_ACCEL_DECODE["preset-nvidia-h264"] = PRESETS_HW_ACCEL_DECODE[
    FFMPEG_HWACCEL_NVIDIA
]
PRESETS_HW_ACCEL_DECODE["preset-nvidia-h265"] = PRESETS_HW_ACCEL_DECODE[
    FFMPEG_HWACCEL_NVIDIA
]
PRESETS_HW_ACCEL_DECODE["preset-nvidia-mjpeg"] = PRESETS_HW_ACCEL_DECODE[
    FFMPEG_HWACCEL_NVIDIA
]

PRESETS_HW_ACCEL_DECODE[FFMPEG_HWACCEL_RKMPP] = (
    f"{PRESETS_HW_ACCEL_DECODE[f'{FFMPEG_HWACCEL_RKMPP}-no-dump_extra']}{' -bsf:v dump_extra' if LIBAVFORMAT_VERSION_MAJOR >= 61 else ''}"
)
PRESETS_HW_ACCEL_DECODE["preset-rk-h264"] = PRESETS_HW_ACCEL_DECODE[
    FFMPEG_HWACCEL_RKMPP
]
PRESETS_HW_ACCEL_DECODE["preset-rk-h265"] = PRESETS_HW_ACCEL_DECODE[
    FFMPEG_HWACCEL_RKMPP
]

# Presets for FFMPEG Stream Scaling (detect role)

PRESETS_HW_ACCEL_SCALE = {
    "preset-rpi-64-h264": "-r {0} -vf fps={0},scale={1}:{2}",
    "preset-rpi-64-h265": "-r {0} -vf fps={0},scale={1}:{2}",
    # ffmpeg's v4l2m2m decoders cannot scale, so frames are scaled on the CPU
    "preset-apple-silicon-h264": "-r {0} -vf fps={0},scale={1}:{2}",
    "preset-apple-silicon-h265": "-r {0} -vf fps={0},scale={1}:{2}",
    FFMPEG_HWACCEL_VAAPI: "-r {0} -vf fps={0},scale_vaapi=w={1}:h={2},hwdownload,format=nv12",
    "preset-intel-qsv-h264": "-r {0} -vf fps={0},vpp_qsv=w={1}:h={2}:format=nv12,hwdownload,format=nv12,format=yuv420p",
    "preset-intel-qsv-h265": "-r {0} -vf fps={0},vpp_qsv=w={1}:h={2}:format=nv12,hwdownload,format=nv12,format=yuv420p",
    FFMPEG_HWACCEL_NVIDIA: "-r {0} -vf fps={0},scale_cuda=w={1}:h={2},hwdownload,format=nv12",
    "preset-jetson-h264": "-r {0}",  # scaled in decoder
    "preset-jetson-h265": "-r {0}",  # scaled in decoder
    FFMPEG_HWACCEL_RKMPP: "-r {0} -vf scale_rkrga=w={1}:h={2}:format=yuv420p:force_original_aspect_ratio=0,hwmap=mode=read,format=yuv420p",
    "default": "-r {0} -vf fps={0},scale={1}:{2}",
    # experimental presets
    FFMPEG_HWACCEL_VULKAN: "-r {0} -vf fps={0},hwupload,scale_vulkan=w={1}:h={2},hwdownload",
    FFMPEG_HWACCEL_AMF: "-r {0} -vf fps={0},hwupload,scale_amf=w={1}:h={2},hwdownload",
}
PRESETS_HW_ACCEL_SCALE["preset-nvidia-h264"] = PRESETS_HW_ACCEL_SCALE[
    FFMPEG_HWACCEL_NVIDIA
]
PRESETS_HW_ACCEL_SCALE["preset-nvidia-h265"] = PRESETS_HW_ACCEL_SCALE[
    FFMPEG_HWACCEL_NVIDIA
]

PRESETS_HW_ACCEL_SCALE[f"{FFMPEG_HWACCEL_RKMPP}-no-dump_extra"] = (
    PRESETS_HW_ACCEL_SCALE[FFMPEG_HWACCEL_RKMPP]
)
PRESETS_HW_ACCEL_SCALE["preset-rk-h264"] = PRESETS_HW_ACCEL_SCALE[FFMPEG_HWACCEL_RKMPP]
PRESETS_HW_ACCEL_SCALE["preset-rk-h265"] = PRESETS_HW_ACCEL_SCALE[FFMPEG_HWACCEL_RKMPP]

# Presets for FFMPEG Stream Encoding (birdseye feature)

PRESETS_HW_ACCEL_ENCODE_BIRDSEYE = {
    "preset-rpi-64-h264": "{0} -hide_banner {1} -c:v h264_v4l2m2m {2}",
    "preset-rpi-64-h265": "{0} -hide_banner {1} -c:v hevc_v4l2m2m {2}",
    "preset-apple-silicon-h264": "{0} -hide_banner {1} -c:v h264_v4l2m2m {2}",
    "preset-apple-silicon-h265": "{0} -hide_banner {1} -c:v h264_v4l2m2m {2}",
    # -vaapi_device is required in addition to -hwaccel_device: this is the only
    # birdseye preset that uses hwupload, and ffmpeg 8 initializes filters before
    # the decoder creates a device, so hwupload cannot see an -hwaccel_device one.
    # See https://github.com/AlexxIT/go2rtc/issues/1984
    FFMPEG_HWACCEL_VAAPI: "{0} -hide_banner -vaapi_device {3} -hwaccel vaapi -hwaccel_output_format vaapi -hwaccel_device {3} {1} -c:v h264_vaapi -g 50 -bf 0 -profile:v high -level:v 4.1 -sei:v 0 -an -vf format=vaapi|nv12,hwupload {2}",
    "preset-intel-qsv-h264": "{0} -hide_banner {1} -c:v h264_qsv -g 50 -bf 0 -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-intel-qsv-h265": "{0} -hide_banner {1} -c:v h264_qsv -g 50 -bf 0 -profile:v main -level:v 4.1 -async_depth:v 1 {2}",
    FFMPEG_HWACCEL_NVIDIA: "{0} -hide_banner {1} -c:v h264_nvenc -g 50 -profile:v high -level:v auto -preset:v p2 -tune:v ll {2}",
    "preset-jetson-h264": "{0} -hide_banner {1} -c:v h264_nvmpi -profile high {2}",
    "preset-jetson-h265": "{0} -hide_banner {1} -c:v h264_nvmpi -profile main {2}",
    FFMPEG_HWACCEL_RKMPP: "{0} -hide_banner {1} -c:v h264_rkmpp -profile:v high {2}",
    "preset-rk-h265": "{0} -hide_banner {1} -c:v hevc_rkmpp -profile:v main {2}",
    FFMPEG_HWACCEL_AMF: "{0} -hide_banner {1} -c:v h264_amf -g 50 -profile:v high {2}",
    "default": "{0} -hide_banner {1} -c:v libx264 -g 50 -profile:v high -level:v 4.1 -preset:v superfast -tune:v zerolatency {2}",
}
# Windows encoders: the same presets minus the Linux device arguments
PRESETS_HW_ACCEL_ENCODE_BIRDSEYE.update(
    {
        "preset-windows-nvenc": "{0} -hide_banner {1} -c:v h264_nvenc -g 50 -profile:v high -level:v auto -preset:v p2 -tune:v ll {2}",
        "preset-windows-qsv": "{0} -hide_banner {1} -c:v h264_qsv -g 50 -bf 0 -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    }
)

PRESETS_HW_ACCEL_ENCODE_BIRDSEYE["preset-nvidia-h264"] = (
    PRESETS_HW_ACCEL_ENCODE_BIRDSEYE[FFMPEG_HWACCEL_NVIDIA]
)
PRESETS_HW_ACCEL_ENCODE_BIRDSEYE["preset-nvidia-h265"] = (
    PRESETS_HW_ACCEL_ENCODE_BIRDSEYE[FFMPEG_HWACCEL_NVIDIA]
)

PRESETS_HW_ACCEL_ENCODE_BIRDSEYE[f"{FFMPEG_HWACCEL_RKMPP}-no-dump_extra"] = (
    PRESETS_HW_ACCEL_ENCODE_BIRDSEYE[FFMPEG_HWACCEL_RKMPP]
)
PRESETS_HW_ACCEL_ENCODE_BIRDSEYE["preset-rk-h264"] = PRESETS_HW_ACCEL_ENCODE_BIRDSEYE[
    FFMPEG_HWACCEL_RKMPP
]

# Presets for FFMPEG Stream Encoding (timelapse feature)

PRESETS_HW_ACCEL_ENCODE_TIMELAPSE = {
    "preset-rpi-64-h264": "{0} -hide_banner {1} -c:v h264_v4l2m2m -pix_fmt yuv420p {2}",
    "preset-rpi-64-h265": "{0} -hide_banner {1} -c:v hevc_v4l2m2m -pix_fmt yuv420p {2}",
    "preset-apple-silicon-h264": "{0} -hide_banner {1} -c:v h264_v4l2m2m -pix_fmt yuv420p {2}",
    "preset-apple-silicon-h265": "{0} -hide_banner {1} -c:v hevc_v4l2m2m -pix_fmt yuv420p {2}",
    FFMPEG_HWACCEL_VAAPI: "{0} -hide_banner -hwaccel vaapi -hwaccel_output_format vaapi -hwaccel_device {3} {1} -c:v h264_vaapi {2}",
    "preset-intel-qsv-h264": "{0} -hide_banner {1} -c:v h264_qsv -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    "preset-intel-qsv-h265": "{0} -hide_banner {1} -c:v hevc_qsv -profile:v main -level:v 4.1 -async_depth:v 1 {2}",
    FFMPEG_HWACCEL_NVIDIA: "{0} -hide_banner -hwaccel cuda -hwaccel_output_format cuda -extra_hw_frames 8 {1} -c:v h264_nvenc {2}",
    "preset-nvidia-h265": "{0} -hide_banner -hwaccel cuda -hwaccel_output_format cuda -extra_hw_frames 8 {1} -c:v hevc_nvenc {2}",
    "preset-jetson-h264": "{0} -hide_banner {1} -c:v h264_nvmpi -profile high {2}",
    "preset-jetson-h265": "{0} -hide_banner {1} -c:v hevc_nvmpi -profile main {2}",
    FFMPEG_HWACCEL_RKMPP: "{0} -hide_banner {1} -c:v h264_rkmpp -profile:v high {2}",
    "preset-rk-h265": "{0} -hide_banner {1} -c:v hevc_rkmpp -profile:v main {2}",
    FFMPEG_HWACCEL_AMF: "{0} -hide_banner {1} -c:v h264_amf -profile:v high {2}",
    "default": "{0} -hide_banner {1} -c:v libx264 -preset:v ultrafast -tune:v zerolatency {2}",
}
PRESETS_HW_ACCEL_ENCODE_TIMELAPSE.update(
    {
        "preset-windows-nvenc": "{0} -hide_banner -hwaccel cuda -extra_hw_frames 8 {1} -c:v h264_nvenc {2}",
        "preset-windows-qsv": "{0} -hide_banner {1} -c:v h264_qsv -profile:v high -level:v 4.1 -async_depth:v 1 {2}",
    }
)

PRESETS_HW_ACCEL_ENCODE_TIMELAPSE["preset-nvidia-h264"] = (
    PRESETS_HW_ACCEL_ENCODE_TIMELAPSE[FFMPEG_HWACCEL_NVIDIA]
)

PRESETS_HW_ACCEL_ENCODE_TIMELAPSE[f"{FFMPEG_HWACCEL_RKMPP}-no-dump_extra"] = (
    PRESETS_HW_ACCEL_ENCODE_TIMELAPSE[FFMPEG_HWACCEL_RKMPP]
)
PRESETS_HW_ACCEL_ENCODE_TIMELAPSE["preset-rk-h264"] = PRESETS_HW_ACCEL_ENCODE_TIMELAPSE[
    FFMPEG_HWACCEL_RKMPP
]

# encoding of previews is only done on CPU due to comparable encode times and better quality from libx264
PRESETS_HW_ACCEL_ENCODE_PREVIEW = {
    "default": "{0} -hide_banner {1} -c:v libx264 -profile:v baseline -preset:v ultrafast {2}",
}


def parse_preset_hardware_acceleration_decode(
    arg: Any,
    fps: int,
    width: int,
    height: int,
    gpu: int,
) -> list[str] | None:
    """Return the correct preset if in preset format otherwise return None."""
    if not isinstance(arg, str):
        return None

    decode = PRESETS_HW_ACCEL_DECODE.get(arg, None)

    if not decode:
        return None

    gpu_arg = _gpu_selector.get_gpu_arg(arg, gpu)
    return decode.format(fps, width, height, gpu_arg).split(" ")


def parse_preset_hardware_acceleration_scale(
    arg: Any,
    detect_args: list[str],
    fps: int,
    width: int,
    height: int,
) -> list[str]:
    """Return the correct scaling preset or default preset if none is set."""
    if not isinstance(arg, str) or " " in arg:
        scale = PRESETS_HW_ACCEL_SCALE["default"]
    else:
        scale = PRESETS_HW_ACCEL_SCALE.get(arg, PRESETS_HW_ACCEL_SCALE["default"])

    scale_args = scale.format(fps, width, height).split(" ")
    scale_args.extend(detect_args)
    return scale_args


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
    """Return the correct scaling preset or default preset if none is set."""
    if type == EncodeTypeEnum.birdseye:
        arg_map = PRESETS_HW_ACCEL_ENCODE_BIRDSEYE
    elif type == EncodeTypeEnum.preview:
        arg_map = PRESETS_HW_ACCEL_ENCODE_PREVIEW
    elif type == EncodeTypeEnum.timelapse:
        arg_map = PRESETS_HW_ACCEL_ENCODE_TIMELAPSE

    if not isinstance(arg, str):
        return arg_map["default"].format(quote_ffmpeg_arg(ffmpeg_path), input, output)

    # This preset is Linux-only, and not all Jetsons have its HW encoder.
    # Fall back to software when unsupported, without probing /dev on Windows.
    if arg.startswith("preset-jetson-") and (
        IS_WINDOWS or not os.path.exists("/dev/nvhost-msenc")
    ):
        arg = "default"

    return arg_map.get(arg, arg_map["default"]).format(
        quote_ffmpeg_arg(ffmpeg_path),
        input,
        output,
        _gpu_selector.get_gpu_arg(arg, 0),
    )


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
# preset validation
# ---------------------------------------------------------------------------

#: presets whose implementation contains a platform-specific API or device
#: argument that cannot work on Windows. The validator names the Windows
#: preset to use instead rather than letting ffmpeg fail at startup.
LINUX_ONLY_DECODE_PRESETS = {
    FFMPEG_HWACCEL_VAAPI: "preset-windows-d3d11va",
    "preset-intel-qsv-h264": "preset-windows-qsv-h264",
    "preset-intel-qsv-h265": "preset-windows-qsv-h265",
    FFMPEG_HWACCEL_RKMPP: "preset-windows-d3d11va",
    f"{FFMPEG_HWACCEL_RKMPP}-no-dump_extra": "preset-windows-d3d11va",
    "preset-rk-h264": "preset-windows-d3d11va",
    "preset-rk-h265": "preset-windows-d3d11va",
}

#: decode presets that only exist for another platform's hardware
OTHER_PLATFORM_DECODE_PRESETS = (
    "preset-rpi-64-h264",
    "preset-rpi-64-h265",
    "preset-apple-silicon-h264",
    "preset-apple-silicon-h265",
    "preset-jetson-h264",
    "preset-jetson-h265",
)


def windows_decode_presets() -> list[str]:
    """Every decode preset that works on Windows, in recommendation order."""
    return [
        "preset-windows-d3d11va",
        "preset-windows-nvenc",
        "preset-windows-qsv",
        "preset-windows-qsv-h264",
        "preset-windows-qsv-h265",
        "preset-windows-dxva2",
        "preset-amd-amf",
        "preset-vulkan",
    ]


#: decode presets that carry -hwaccel_device and therefore a Linux DRM device
NVIDIA_DRM_DECODE_PRESETS = (
    FFMPEG_HWACCEL_NVIDIA,
    "preset-nvidia-h264",
    "preset-nvidia-h265",
    "preset-nvidia-mjpeg",
)


def _valid_presets(setting: str, presets: dict[str, Any] | list[str]) -> list[str]:
    """Preset names to offer in an error message for a setting."""
    if IS_WINDOWS and setting == "ffmpeg.hwaccel_args":
        return windows_decode_presets()

    return sorted(presets)


def _preset_error(
    value: str, setting: str, presets: dict[str, Any] | list[str]
) -> str | None:
    """Return why a preset value cannot be used, or None when it is fine."""
    if not value.startswith("preset-"):
        return None

    if value not in presets:
        return (
            f"{value} is not a known preset for {setting}, "
            f"valid presets are {', '.join(_valid_presets(setting, presets))}"
        )

    if not IS_WINDOWS or setting != "ffmpeg.hwaccel_args":
        return None

    if value in LINUX_ONLY_DECODE_PRESETS:
        return (
            f"{value} is a Linux only preset and cannot decode video on Windows, "
            f"use {LINUX_ONLY_DECODE_PRESETS[value]}"
        )

    if value in OTHER_PLATFORM_DECODE_PRESETS:
        return (
            f"{value} is a preset for hardware that does not exist on Windows, "
            f"use one of {', '.join(windows_decode_presets())}"
        )

    if value in NVIDIA_DRM_DECODE_PRESETS:
        return (
            f"{value} needs a Linux DRM device and does not work on Windows, "
            "use preset-windows-nvenc"
        )

    return None


_WINDOWS_UNSUPPORTED_HWACCEL = {
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

_WINDOWS_UNSUPPORTED_CODEC_MARKERS = (
    "_v4l2m2m",
    "_nvmpi",
    "_rkmpp",
    "_vaapi",
    "_omx",
    "_videotoolbox",
    "_mmal",
    "_mediacodec",
)
_WINDOWS_LINUX_DEVICE_PREFIXES = (
    "/dev/dri/",
    "/dev/video",
    "/dev/v4l/",
    "/dev/nvidia",
    "/dev/nvhost",
)


def _windows_raw_hardware_error(args: list[str], setting: str) -> str | None:
    """Explain why raw Linux or macOS hardware arguments cannot run on Windows."""
    tokens: list[str] = []

    for arg in args:
        try:
            tokens.extend(split_ffmpeg_command(arg))
        except ValueError:
            tokens.extend(arg.split())

    lowered = [token.lower() for token in tokens]

    for index, token in enumerate(lowered):
        if token == "-f" and index + 1 < len(lowered):
            input_format = lowered[index + 1]
        elif token.startswith("-f="):
            input_format = token.split("=", maxsplit=1)[1]
        else:
            input_format = None

        if input_format in ("v4l2", "video4linux2"):
            return (
                "The V4L2 FFmpeg input format is Linux-only and cannot be used on "
                "native Windows. Use a DirectShow input or a network stream instead."
            )

        backend = None

        if token in ("-hwaccel", "-hwaccel_output_format", "-init_hw_device"):
            if index + 1 < len(lowered):
                backend = lowered[index + 1].split("=", maxsplit=1)[0]
        elif token.startswith(("-hwaccel=", "-hwaccel_output_format=")):
            backend = token.split("=", maxsplit=1)[1].split("=", maxsplit=1)[0]

        if backend in _WINDOWS_UNSUPPORTED_HWACCEL:
            if setting == "ffmpeg.output_args.record":
                return (
                    f"{backend} is not available for native Windows recording. "
                    "Use a Windows-compatible output encoder instead."
                )

            replacement = _WINDOWS_UNSUPPORTED_HWACCEL[backend]
            return (
                f"{backend} is not available on native Windows. Use {replacement} "
                "or another Windows decode preset."
            )

        device_path = token

        if token.startswith(("-hwaccel_device=", "-vaapi_device=")):
            device_path = token.split("=", maxsplit=1)[1]

        if token == "-vaapi_device" or device_path.startswith(
            _WINDOWS_LINUX_DEVICE_PREFIXES
        ):
            if setting == "ffmpeg.output_args.record":
                return (
                    f"{tokens[index]} is a Linux-only hardware device path and "
                    "cannot be used for native Windows recording."
                )

            return (
                f"{tokens[index]} is a Linux-only hardware device path and cannot "
                "be used on native Windows. Use a Windows decode preset instead."
            )

        if "drm_prime" in token:
            return (
                f"{tokens[index]} uses a Linux-only FFmpeg pixel format that is "
                "not available on native Windows. Use a Windows-supported pixel format instead."
            )

        if any(marker in token for marker in _WINDOWS_UNSUPPORTED_CODEC_MARKERS):
            return (
                f"{tokens[index]} uses a platform-specific FFmpeg codec that is "
                "not available on native Windows. Use a Windows-supported codec instead."
            )

        if any(
            marker in token for marker in ("scale_vaapi", "format=vaapi", "scale_rkrga")
        ):
            return (
                f"{tokens[index]} is a Linux-only FFmpeg filter and cannot run on "
                "native Windows. Use Windows-supported FFmpeg filters instead."
            )

    return None


def validate_ffmpeg_platform_args(value: str | list[str] | None, setting: str) -> None:
    """Reject raw hardware arguments for a platform that cannot run them."""
    if not IS_WINDOWS or value is None:
        return

    args = [value] if isinstance(value, str) else value
    error = _windows_raw_hardware_error(args, setting)

    if error:
        raise ValueError(error)


def validate_ffmpeg_args(
    value: str | list[str] | None, setting: str, presets: dict[str, Any] | list[str]
) -> None:
    """Raise when a value contains a preset that cannot be used here.

    Args:
        value: The configured value, either the raw arguments or a preset name
        setting: Config path the value came from, used in the error message
        presets: The preset map valid for this setting

    Raises:
        ValueError: When a preset name is unknown, or cannot work on this platform
    """
    if value is None:
        return

    args = [value] if isinstance(value, str) else value

    for arg in args:
        error = _preset_error(arg, setting, presets)

        if error:
            raise ValueError(error)

    if setting.startswith("ffmpeg."):
        validate_ffmpeg_platform_args(args, setting)
