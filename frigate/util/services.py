"""Native Windows service, diagnostics, and media-probe utilities."""

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import subprocess as sp
import time
from datetime import datetime
from enum import Enum
from typing import Any

import cv2
import psutil
import py3nvml.py3nvml as nvml
import requests

from frigate.const import SHM_FRAMES_VAR
from frigate.util.builtin import clean_camera_user_pass, escape_special_characters
from frigate.util.env import UnknownVariableError, substitute_frigate_vars
from frigate.util.platform import (
    get_tmp_dir,
    resolve_ffmpeg_binary,  # noqa: F401 - public compatibility import
)

logger = logging.getLogger(__name__)
RESTART_MARKER_NAME = "restart.requested"
PROCESS_KEYWORDS = ("ffmpeg", "go2rtc", "frigate.", "python.exe")


def _stop_self() -> None:
    """Invoke the installed SIGINT handler for a graceful Windows shutdown."""
    signal.raise_signal(signal.SIGINT)


def _mark_restart_requested() -> None:
    try:
        path = os.path.join(get_tmp_dir(), RESTART_MARKER_NAME)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write("restart")
    except OSError as err:
        logger.debug("Unable to write the restart marker: %s", err)


def consume_restart_request() -> bool:
    path = os.path.join(get_tmp_dir(), RESTART_MARKER_NAME)
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    except OSError as err:
        logger.debug("Unable to clear restart marker %s: %s", path, err)
        return True
    return True


def restart_frigate() -> None:
    """Request restart from the PowerShell launcher or SCM wrapper."""
    _mark_restart_requested()
    _stop_self()


def _process_cpu_times(process: psutil.Process) -> tuple[float, float, float]:
    times = process.cpu_times()
    return float(times.user), float(times.system), float(process.create_time())


def _process_cpu_average(user: float, system: float, start_time: float) -> float:
    return (user + system) * 100 / max(time.time() - start_time, 1.0)


def _process_resident_memory(process: psutil.Process) -> float:
    return process.memory_info().rss / 1024


def get_cpu_stats() -> dict[str, dict]:
    """Return system and Frigate-related Windows process usage."""
    usages: dict[str, dict] = {}
    total_mem = psutil.virtual_memory().total / 1024
    memory = psutil.virtual_memory()
    usages["frigate.full_system"] = {
        "cpu": str(psutil.cpu_percent(interval=None)),
        "mem": str(memory.percent),
    }

    for process in psutil.process_iter(
        ["pid", "name", "cpu_percent", "cmdline", "create_time"]
    ):
        try:
            process_name = str(process.info["name"] or "")
            cmdline = " ".join(process.info["cmdline"] or []).rstrip()
            searchable = f"{process_name} {cmdline}".casefold()
            if not any(keyword.casefold() in searchable for keyword in PROCESS_KEYWORDS):
                continue
            user, system, started = _process_cpu_times(process)
            mem_pct = round((_process_resident_memory(process) / total_mem) * 100, 1)
            usages[str(process.info["pid"])] = {
                "cpu": str(process.info["cpu_percent"]),
                "cpu_average": str(round(_process_cpu_average(user, system, started), 2)),
                "mem": str(mem_pct),
                "name": process_name,
                "cmdline": clean_camera_user_pass(cmdline),
            }
        except (psutil.Error, OSError, TypeError, ValueError):
            continue
    return usages


def try_get_info(function, handle, default="N/A", sensor=None):
    try:
        if handle:
            return function(handle, sensor) if sensor is not None else function(handle)
        return function()
    except nvml.NVMLError_NotSupported:
        return default


def _nvml_text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def get_nvidia_gpu_stats() -> dict[int, dict]:
    """Read Windows NVIDIA utilization through NVML."""
    names: dict[str, int] = {}
    results: dict[int, dict] = {}
    try:
        nvml.nvmlInit()
        for index in range(nvml.nvmlDeviceGetCount()):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            base_name = _nvml_text(nvml.nvmlDeviceGetName(handle))
            names[base_name] = names.get(base_name, 0) + 1
            name = base_name if names[base_name] == 1 else f"{base_name} ({names[base_name]})"
            memory = try_get_info(nvml.nvmlDeviceGetMemoryInfo, handle)
            utilization = try_get_info(nvml.nvmlDeviceGetUtilizationRates, handle)
            encoder = try_get_info(nvml.nvmlDeviceGetEncoderUtilization, handle)
            decoder = try_get_info(nvml.nvmlDeviceGetDecoderUtilization, handle)
            temperature = try_get_info(
                nvml.nvmlDeviceGetTemperature, handle, default=None, sensor=0
            )
            power_state = try_get_info(nvml.nvmlDeviceGetPowerState, handle, default=None)
            results[index] = {
                "name": name,
                "gpu": utilization.gpu if utilization != "N/A" else 0,
                "mem": memory.used / memory.total * 100 if memory != "N/A" else -1,
                "enc": encoder[0] if encoder != "N/A" else -1,
                "dec": decoder[0] if decoder != "N/A" else -1,
                "pstate": power_state or "unknown",
                "temp": float(temperature) if temperature is not None else None,
            }
    except Exception:
        logger.debug("Unable to query NVIDIA GPU stats", exc_info=True)
    return results

# Snapshot: environment_vars lands in os.environ after import and must not
# be able to enable this.
_GO2RTC_ARBITRARY_EXEC_ENV = os.environ.get("GO2RTC_ALLOW_ARBITRARY_EXEC")


def is_go2rtc_arbitrary_exec_allowed() -> bool:
    """Read the process-start Windows environment override."""
    raw = _GO2RTC_ARBITRARY_EXEC_ENV
    return raw is not None and str(raw).lower() in ("true", "1", "yes")


def is_restricted_go2rtc_source(stream_source: str) -> bool:
    """Check if a stream source is a restricted type (echo, expr, or exec)
    and the GO2RTC_ALLOW_ARBITRARY_EXEC override is not set."""
    if not stream_source.strip().startswith(("echo:", "expr:", "exec:")):
        return False
    return not is_go2rtc_arbitrary_exec_allowed()


# go2rtc expands an `ffmpeg:` source into an `exec:` command line built from its
# `#key=value` parameters, so only parameters that map to fixed presets or plain
# numbers are accepted from the API
_GO2RTC_FFMPEG_NAME_PARAMS = frozenset({"video", "audio", "hardware"})
_GO2RTC_FFMPEG_NUMERIC_PARAMS = frozenset(
    {"width", "height", "rotate", "bitrate", "timeout"}
)
_GO2RTC_FFMPEG_FLAG_PARAMS = frozenset({"async"})
# a preset or codec name, optionally with a sample rate (e.g. opus/16000)
_GO2RTC_FFMPEG_NAME_VALUE = re.compile(r"^([A-Za-z0-9_]*|[A-Za-z0-9_]+/[0-9]{1,6})$")
_GO2RTC_FFMPEG_NUMERIC_VALUE = re.compile(r"^-?[0-9]+[kKmM]?$")


def _is_safe_go2rtc_ffmpeg_source(stream_source: str) -> bool:
    """Check that an `ffmpeg:` source only uses allowlisted parameters."""
    if any(c in stream_source for c in "'\"") or re.search(r"\s", stream_source):
        return False

    _, *params = stream_source.split("#")
    for param in params:
        key, has_value, value = param.partition("=")
        if key in _GO2RTC_FFMPEG_NAME_PARAMS:
            if not _GO2RTC_FFMPEG_NAME_VALUE.match(value):
                return False
        elif key in _GO2RTC_FFMPEG_NUMERIC_PARAMS:
            if not _GO2RTC_FFMPEG_NUMERIC_VALUE.match(value):
                return False
        elif key in _GO2RTC_FFMPEG_FLAG_PARAMS:
            if has_value:
                return False
        else:
            return False

    return True


def is_restricted_go2rtc_api_source(stream_source: str) -> bool:
    """Check if a stream source submitted through the API is restricted.

    In addition to the echo/expr/exec sources, this restricts `ffmpeg:` sources
    whose parameters could inject arbitrary ffmpeg arguments. The
    GO2RTC_ALLOW_ARBITRARY_EXEC override lifts both restrictions.
    """
    source = stream_source.strip()
    if is_restricted_go2rtc_source(source):
        return True
    if not source.startswith("ffmpeg:"):
        return False
    if _is_safe_go2rtc_ffmpeg_source(source[len("ffmpeg:") :]):
        return False
    return not is_go2rtc_arbitrary_exec_allowed()


def _go2rtc_stream_sources(stream: Any) -> list[Any]:
    return stream if isinstance(stream, list) else [stream]


class Go2rtcStreamIssue(str, Enum):
    """Why a go2rtc stream source was rejected."""

    restricted = "restricted"
    unknown_variable = "unknown_variable"
    invalid_placeholder = "invalid_placeholder"


def find_restricted_go2rtc_stream_changes(
    old_streams: dict[str, Any] | None, new_streams: dict[str, Any] | None
) -> dict[str, Go2rtcStreamIssue]:
    """Return the go2rtc streams with new sources that must be rejected.

    Used when the config is changed through the API. A source that is already
    in the saved config for the same stream is left alone, so streams the
    operator configured on disk keep working; any other source must resolve
    its {FRIGATE_*} placeholders and pass `is_restricted_go2rtc_api_source`.
    """
    old_streams = old_streams if isinstance(old_streams, dict) else {}
    new_streams = new_streams if isinstance(new_streams, dict) else {}
    issues: dict[str, Go2rtcStreamIssue] = {}

    for name, stream in new_streams.items():
        existing = _go2rtc_stream_sources(old_streams.get(name))

        for source in _go2rtc_stream_sources(stream):
            if source in existing:
                continue

            if not isinstance(source, str):
                # the map form is only usable with arbitrary exec enabled
                if is_go2rtc_arbitrary_exec_allowed():
                    continue
                issues[name] = Go2rtcStreamIssue.restricted
                break

            # only sources whose placeholders resolve here are accepted, so the
            # resolved value checked below is what go2rtc loads at startup
            try:
                resolved_source = substitute_frigate_vars(source)
            except UnknownVariableError:
                issues[name] = Go2rtcStreamIssue.unknown_variable
                break
            except ValueError:
                issues[name] = Go2rtcStreamIssue.invalid_placeholder
                break

            if is_restricted_go2rtc_api_source(
                source
            ) or is_restricted_go2rtc_api_source(resolved_source):
                issues[name] = Go2rtcStreamIssue.restricted
                break

    return issues


def ffprobe_stream(ffmpeg, path: str, detailed: bool = False) -> sp.CompletedProcess:
    """Run ffprobe on stream."""
    clean_path = escape_special_characters(path)

    # Base entries that are always included
    stream_entries = "codec_long_name,width,height,bit_rate,duration,display_aspect_ratio,avg_frame_rate"

    # Additional detailed entries
    if detailed:
        stream_entries += ",codec_name,profile,level,pix_fmt,channels,sample_rate,channel_layout,r_frame_rate"
        format_entries = "format_name,size,bit_rate,duration"
    else:
        format_entries = None

    def run(rtsp_transport: str | None = None) -> sp.CompletedProcess:
        cmd = [ffmpeg.ffprobe_path]
        if rtsp_transport:
            cmd += ["-rtsp_transport", rtsp_transport]
        cmd += [
            "-timeout",
            "1000000",
            "-print_format",
            "json",
            "-show_entries",
            f"stream={stream_entries}",
        ]
        if detailed and format_entries:
            cmd.extend(["-show_entries", f"format={format_entries}"])
        cmd.extend(["-loglevel", "error", clean_path])
        try:
            return sp.run(cmd, capture_output=True, timeout=6)
        except sp.TimeoutExpired as e:
            logger.info(
                "ffprobe timed out while probing %s (transport=%s)",
                clean_camera_user_pass(path),
                rtsp_transport or "default",
            )
            return sp.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout=e.stdout or b"",
                stderr=(e.stderr or b"") + b"\nffprobe timed out",
            )

    result = run()

    # For RTSP: retry with explicit TCP transport if the first attempt failed
    # (default UDP may be blocked)
    if result.returncode != 0 and clean_path.startswith("rtsp://"):
        result = run(rtsp_transport="tcp")

    return result


KEYFRAME_PROBE_WINDOW_SECONDS = 20
KEYFRAME_GAP_WARNING_SECONDS = 4.0
KEYFRAME_GAP_JITTER_SECONDS = 0.5


def parse_keyframe_packets(output: str) -> tuple[list[float], float | None]:
    """Parse ffprobe CSV `pts_time,flags` output.

    Returns the presentation timestamps of keyframes (flags containing "K")
    and the maximum timestamp observed across all packets.
    """
    keyframe_pts: list[float] = []
    max_pts: float | None = None

    for line in output.splitlines():
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            pts = float(parts[0])
        except ValueError:
            continue
        if max_pts is None or pts > max_pts:
            max_pts = pts
        if "K" in parts[1]:
            keyframe_pts.append(pts)

    return keyframe_pts, max_pts


def classify_keyframe_gaps(
    keyframe_pts: list[float], segment_time: int
) -> dict[str, Any]:
    """Classify keyframe spacing for recording suitability.

    A camera using a smart/+ codec or a long/variable GOP produces large or
    irregular gaps between keyframes, which breaks time-based recording
    segmentation. Severity:
      - "unknown" when fewer than two keyframes were observed
      - "error" when the longest gap exceeds the record segment length
      - "warning" when the longest gap exceeds the warning threshold
      - "ok" otherwise

    The "pattern" key separates the two causes so callers can give accurate
    advice: "fixed" is a regular GOP that is simply too long, "variable" is
    the irregular spacing a smart/+ codec produces.
    """
    thresholds = {
        "warning": KEYFRAME_GAP_WARNING_SECONDS,
        "error": segment_time,
    }

    if len(keyframe_pts) < 2:
        return {
            "keyframe_count": len(keyframe_pts),
            "max_gap": None,
            "mean_gap": None,
            "min_gap": None,
            "pattern": None,
            "segment_time": segment_time,
            "severity": "unknown",
            "thresholds": thresholds,
        }

    gaps = [b - a for a, b in zip(keyframe_pts, keyframe_pts[1:])]
    max_gap = max(gaps)
    min_gap = min(gaps)

    if max_gap > segment_time:
        severity = "error"
    elif max_gap > KEYFRAME_GAP_WARNING_SECONDS:
        severity = "warning"
    else:
        severity = "ok"

    # allow for encoder jitter and probe rounding before calling a GOP variable
    tolerance = max(KEYFRAME_GAP_JITTER_SECONDS, min_gap * 0.25)
    pattern = "variable" if (max_gap - min_gap) > tolerance else "fixed"

    return {
        "keyframe_count": len(keyframe_pts),
        "max_gap": round(max_gap, 2),
        "mean_gap": round(sum(gaps) / len(gaps), 2),
        "min_gap": round(min_gap, 2),
        "pattern": pattern,
        "segment_time": segment_time,
        "severity": severity,
        "thresholds": thresholds,
    }


async def analyze_record_keyframes(
    ffmpeg, url: str, segment_time: int, window: int = KEYFRAME_PROBE_WINDOW_SECONDS
) -> dict[str, Any]:
    """Probe a stream for ~`window` seconds and classify its keyframe spacing.

    Reads video packet flags via ffprobe to find keyframes, then measures the
    gaps between them. On timeout or failure returns an "unknown" result rather
    than a false all-clear.
    """
    clean_url = escape_special_characters(url)
    cmd = [
        ffmpeg.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        f"%+{window}",
        "-show_entries",
        "packet=pts_time,flags",
        "-of",
        "csv=p=0",
        clean_url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=window + 15)
    except TimeoutError:
        logger.warning("Keyframe probe timed out for record stream")
        proc.kill()
        return classify_keyframe_gaps([], segment_time)
    except OSError as err:
        logger.error("Keyframe probe failed: %s", err)
        return classify_keyframe_gaps([], segment_time)

    keyframe_pts, max_pts = parse_keyframe_packets(stdout.decode("utf-8", "replace"))
    result = classify_keyframe_gaps(keyframe_pts, segment_time)
    result["duration_observed"] = round(max_pts, 2) if max_pts is not None else None
    return result


def get_nvidia_driver_info() -> dict[str, Any]:
    """Get general hardware info for nvidia GPU."""
    results = {}
    try:
        nvml.nvmlInit()
        deviceCount = nvml.nvmlDeviceGetCount()
        for i in range(deviceCount):
            handle = nvml.nvmlDeviceGetHandleByIndex(i)
            driver = try_get_info(nvml.nvmlSystemGetDriverVersion, None, default=None)
            cuda_compute = try_get_info(
                nvml.nvmlDeviceGetCudaComputeCapability, handle, default=None
            )
            vbios = try_get_info(nvml.nvmlDeviceGetVbiosVersion, handle, default=None)
            results[i] = {
                "name": nvml.nvmlDeviceGetName(handle),
                "driver": driver or "unknown",
                "cuda_compute": cuda_compute or "unknown",
                "vbios": vbios or "unknown",
            }
    except Exception:
        pass
    finally:
        return results


def get_go2rtc_hardware_sources() -> list[dict[str, str]]:
    """Ask go2rtc which ffmpeg accelerators it can open.

    Returns an empty list when go2rtc is not running, which is the normal case
    while the config is being loaded.
    """
    try:
        resp = requests.get("http://127.0.0.1:1984/api/ffmpeg/hardware", timeout=3)

        if resp.status_code == 200:
            data: dict[str, list[dict[str, str]]] = resp.json()
            return data.get("sources", [])
    except requests.RequestException:
        pass

    return []


def auto_detect_hwaccel() -> str:
    """Resolve automatic decoding against native Windows accelerators."""
    from frigate.util.hwaccel import windows_hwaccel_preset

    preset = windows_hwaccel_preset(get_go2rtc_hardware_sources())
    if preset:
        logger.info("Automatically detected %s for video decoding", preset)
        return preset

    logger.warning(
        "Did not detect a hardware decoder; set ffmpeg.hwaccel_args to a "
        "preset-windows-* value if this machine has a supported GPU"
    )
    return ""


async def get_video_properties(
    ffmpeg, url: str, get_duration: bool = False
) -> dict[str, Any]:
    async def probe_with_ffprobe(
        url: str,
        rtsp_transport: str | None = None,
    ) -> tuple[
        bool,
        int,
        int,
        str | None,
        str | None,
        float,
        bool | None,
        int | None,
        str | None,
    ]:
        """Probe using ffprobe: returns (valid, width, height, fourcc, video_codec, duration, has_audio, audio_rate, audio_codec).

        ffprobe reports the codec name directly, so fourcc and
        video_codec are the same value on this path.
        """
        cmd = [ffmpeg.ffprobe_path]
        if rtsp_transport:
            cmd += ["-rtsp_transport", rtsp_transport]
        cmd += [
            "-rw_timeout",
            "5000000",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            url,
        ]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6)
            except TimeoutError:
                logger.info(
                    "ffprobe timed out while probing %s (transport=%s)",
                    clean_camera_user_pass(url),
                    rtsp_transport or "default",
                )
                return False, 0, 0, None, None, -1, None, None, None

            if proc.returncode != 0:
                return False, 0, 0, None, None, -1, None, None, None

            data = json.loads(stdout.decode())
            video_streams = [
                s for s in data.get("streams", []) if s.get("codec_type") == "video"
            ]
            if not video_streams:
                return False, 0, 0, None, None, -1, None, None, None

            v = video_streams[0]
            width = int(v.get("width", 0))
            height = int(v.get("height", 0))
            codec = v.get("codec_name")

            duration_str = data.get("format", {}).get("duration")
            duration = float(duration_str) if duration_str else -1.0

            audio_streams = [
                s for s in data.get("streams", []) if s.get("codec_type") == "audio"
            ]
            has_audio = bool(audio_streams)

            # codec and sample rate distinguish audio tracks whose decoder
            # configs cannot share an HLS sequence
            audio_rate: int | None = None
            audio_codec: str | None = None
            if audio_streams:
                try:
                    audio_rate = int(audio_streams[0]["sample_rate"])
                except (KeyError, TypeError, ValueError):
                    audio_rate = None
                audio_codec = audio_streams[0].get("codec_name")

            return (
                True,
                width,
                height,
                codec,
                codec,
                duration,
                has_audio,
                audio_rate,
                audio_codec,
            )
        except (json.JSONDecodeError, ValueError, KeyError, sp.SubprocessError):
            return False, 0, 0, None, None, -1, None, None, None
        finally:
            # callers run in a per-cycle event loop, and an ffprobe still
            # running when that loop closes is finalized against a dead loop
            # ("Event loop is closed"). Draining after the kill is what
            # closes the pipes and their transports
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.communicate(), timeout=2)

    def probe_with_cv2(
        url: str,
    ) -> tuple[
        bool,
        int,
        int,
        str | None,
        str | None,
        float,
        bool | None,
        int | None,
        str | None,
    ]:
        """Probe using cv2: returns (valid, width, height, fourcc, video_codec, duration, has_audio, audio_rate, audio_codec).

        cv2 cannot report audio streams or a normalized codec name, so
        has_audio, audio_rate, audio_codec, and video_codec are always
        None (unknown) on this path.
        """
        cap = cv2.VideoCapture(url)
        if not cap.isOpened():
            cap.release()
            return False, 0, 0, None, None, -1, None, None, None

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        valid = width > 0 and height > 0
        fourcc = None
        duration = -1.0

        if valid:
            fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
            fourcc = fourcc_int.to_bytes(4, "little").decode("latin-1").strip()

            if get_duration:
                fps = cap.get(cv2.CAP_PROP_FPS)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if fps > 0 and total_frames > 0:
                    duration = total_frames / fps

        cap.release()
        return valid, width, height, fourcc, None, duration, None, None, None

    is_rtsp = url.startswith("rtsp://")

    if is_rtsp:
        # skip cv2 for RTSP: its FFmpeg backend has a hardcoded ~30s internal
        # timeout that cannot be shortened per-call, and ffprobe bounded by
        # -rw_timeout handles RTSP probing reliably
        (
            has_video,
            width,
            height,
            fourcc,
            video_codec,
            duration,
            has_audio,
            audio_rate,
            audio_codec,
        ) = await probe_with_ffprobe(url)
    elif get_duration:
        # ffprobe first: segment validation also needs audio presence,
        # which cv2 cannot report
        (
            has_video,
            width,
            height,
            fourcc,
            video_codec,
            duration,
            has_audio,
            audio_rate,
            audio_codec,
        ) = await probe_with_ffprobe(url)

        # fallback to cv2 if needed; audio stays unknown there
        if not has_video or duration < 0:
            (
                has_video,
                width,
                height,
                fourcc,
                video_codec,
                duration,
                has_audio,
                audio_rate,
                audio_codec,
            ) = probe_with_cv2(url)
    else:
        # try cv2 first for local files, HTTP, RTMP
        (
            has_video,
            width,
            height,
            fourcc,
            video_codec,
            duration,
            has_audio,
            audio_rate,
            audio_codec,
        ) = probe_with_cv2(url)

        # fallback to ffprobe if needed
        if not has_video:
            (
                has_video,
                width,
                height,
                fourcc,
                video_codec,
                duration,
                has_audio,
                audio_rate,
                audio_codec,
            ) = await probe_with_ffprobe(url)

    # last resort for RTSP: try TCP transport, since default UDP may be blocked
    if (not has_video or (get_duration and duration < 0)) and is_rtsp:
        (
            has_video,
            width,
            height,
            fourcc,
            video_codec,
            duration,
            has_audio,
            audio_rate,
            audio_codec,
        ) = await probe_with_ffprobe(url, rtsp_transport="tcp")

    result: dict[str, Any] = {"has_valid_video": has_video}
    if has_video:
        result.update(
            {
                "width": width,
                "height": height,
                "has_audio": has_audio,
                "audio_rate": audio_rate,
                "audio_codec": audio_codec,
                "video_codec": video_codec,
            }
        )
        if fourcc:
            result["fourcc"] = fourcc
    if get_duration:
        result["duration"] = duration

    return result


def process_logs(
    contents: str,
    service: str | None = None,
    start: int | None = None,
    end: int | None = None,
) -> tuple[int, list[str]]:
    log_lines = []
    last_message = None
    last_timestamp = None
    repeat_count = 0

    for raw_line in contents.splitlines():
        clean_line = raw_line.strip()

        if len(clean_line) < 10:
            continue

        # Normalize native tool output that does not include a timestamp.
        if "  " not in clean_line:
            clean_line = f"{datetime.now()}  {clean_line}"

        try:
            # Find the position of the first double space to extract timestamp and message
            date_end = clean_line.index("  ")
            timestamp = clean_line[:date_end]
            full_message = clean_line[date_end:].strip()

            # For frigate, remove the date part from message comparison
            if service == "frigate":
                # Skip the date at the start of the message if it exists
                date_parts = full_message.split("]", 1)
                if len(date_parts) > 1:
                    message_part = date_parts[1].strip()
                else:
                    message_part = full_message
            else:
                message_part = full_message

            if message_part == last_message:
                repeat_count += 1
                continue
            else:
                if repeat_count > 0:
                    # Insert a deduplication message formatted the same way as logs
                    dedup_message = f"{last_timestamp}  [LOGGING] Last message repeated {repeat_count} times"
                    log_lines.append(dedup_message)
                    repeat_count = 0

                log_lines.append(clean_line)
                last_timestamp = timestamp

                last_message = message_part

        except ValueError:
            # If we can't parse the line properly, just add it as is
            log_lines.append(clean_line)
            continue

    # If there were repeated messages at the end, log the count
    if repeat_count > 0:
        dedup_message = (
            f"{last_timestamp}  [LOGGING] Last message repeated {repeat_count} times"
        )
        log_lines.append(dedup_message)

    return len(log_lines), log_lines[start:end]


def set_file_limit() -> None:
    """Windows manages handle limits dynamically; no RLIMIT adjustment exists."""


def calculate_shm_requirements(config) -> dict:
    """Describe page-file-backed shared memory used by decoded frames."""
    memory = psutil.virtual_memory()
    total_mb = round(memory.total / pow(2, 20), 1)
    free_mb = round(memory.available / pow(2, 20), 1)

    reserved_mb = 50 + (8 if config.birdseye.restream else 0)
    frame_size_mb = 0.0
    for camera in config.cameras.values():
        if camera.enabled_in_config and camera.detect.width and camera.detect.height:
            frame_size_mb += round(
                (camera.detect.width * camera.detect.height * 1.5 + 270480) / 1048576,
                1,
            )

    # Reserve capacity for two cameras added at runtime.
    frame_size_mb += 2 * round((1280 * 720 * 1.5 + 270480) / 1048576, 1)
    configured_frames = int(os.environ.get(SHM_FRAMES_VAR, "50"))

    return {
        "total": total_mb,
        "used": round(total_mb - free_mb, 1),
        "free": free_mb,
        "mount_type": "pagefile",
        "page_file_backed": True,
        "available": round(total_mb - reserved_mb, 1),
        "camera_frame_size": frame_size_mb,
        "shm_frame_count": configured_frames,
        "min_shm": round(reserved_mb + frame_size_mb * 20),
    }
