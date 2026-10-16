"""Native Windows hardware usage collection.

CPU counters come from psutil and NVIDIA counters come from NVML. Windows does
not expose Linux sysfs/DRM metrics, so Intel and AMD adapters are discovered for
configuration and video acceleration but are not reported with invented usage
values here.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from frigate.config import FrigateConfig
from frigate.config.camera.updater import (
    CameraConfigUpdateEnum,
    CameraConfigUpdateSubscriber,
)
from frigate.detectors.hardware import hardware_prober
from frigate.util.services import get_cpu_stats, get_nvidia_gpu_stats

logger = logging.getLogger(__name__)

HARDWARE_ERROR_COOLDOWN_SECONDS = 3600
POLL_TIMEOUT_SECONDS = 10


@dataclass
class HardwarePollResult:
    gpu: dict[str, dict[str, Any]] = field(default_factory=dict)
    npu: dict[str, dict[str, Any]] = field(default_factory=dict)
    ok: bool = True


def _present_hardware() -> set[str]:
    return {hardware.key for hardware in hardware_prober.probe()}


def _present_gpu() -> str | None:
    """Return the pollable GPU vendor available to this process."""
    return "nvidia" if "onnx:nvidia" in _present_hardware() else None


def _poll_nvidia(config: FrigateConfig) -> HardwarePollResult:
    usage = get_nvidia_gpu_stats()
    if not usage:
        return HardwarePollResult(
            gpu={"nvidia-gpu": {"vendor": "nvidia", "gpu": "", "mem": ""}},
            ok=False,
        )

    gpu: dict[str, dict[str, Any]] = {}
    for entry in usage.values():
        gpu[entry["name"]] = {
            "vendor": "nvidia",
            "gpu": f"{round(float(entry['gpu']), 2)}%",
            "mem": f"{round(float(entry['mem']), 2)}%",
            "enc": f"{round(float(entry['enc']), 2)}%",
            "dec": f"{round(float(entry['dec']), 2)}%",
            "temp": str(entry["temp"]),
        }
    return HardwarePollResult(gpu=gpu)


POLLERS: dict[str, Callable[[FrigateConfig], HardwarePollResult]] = {
    "nvidia": _poll_nvidia,
}


def _hwaccel_hardware(args: str) -> str | None:
    lowered = args.lower()
    if "windows-nvenc" in lowered or "-hwaccel cuda" in lowered:
        return "nvidia"
    return None


class HardwareStats:
    """Catalog and poll usage counters available through Windows APIs."""

    def __init__(self, config: FrigateConfig) -> None:
        self.config = config
        self._monitored: dict[str, Callable[[], HardwarePollResult]] = {}
        self._errors: dict[str, float] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="hw_stats"
        )
        self._config_subscriber = CameraConfigUpdateSubscriber(
            config,
            config.cameras,
            [
                CameraConfigUpdateEnum.add,
                CameraConfigUpdateEnum.remove,
                CameraConfigUpdateEnum.ffmpeg,
                CameraConfigUpdateEnum.audio_transcription,
                CameraConfigUpdateEnum.semantic_search,
                CameraConfigUpdateEnum.face_recognition,
                CameraConfigUpdateEnum.lpr,
            ],
        )
        self.update_config()

    def set_config(self, config: FrigateConfig) -> None:
        self.config = config
        self._config_subscriber.config = config
        self._config_subscriber.camera_configs = config.cameras
        self.update_config()

    def update_config(self) -> None:
        names = self._scan_ffmpeg() | self._scan_detectors() | self._scan_enrichments()
        if not self.config.telemetry.stats.nvidia_gpu_stats:
            names.discard("nvidia")
        self._monitored = {
            name: partial(POLLERS[name], self.config)
            for name in sorted(names)
            if name in POLLERS
        }
        logger.debug("Monitoring Windows hardware stats: %s", list(self._monitored))

    def update_stats(self, all_stats: dict[str, Any]) -> None:
        if self._config_subscriber.check_for_updates():
            self.update_config()

        now = time.monotonic()
        hardware_futures: dict[Future[HardwarePollResult], str] = {}
        for name, poll in self._monitored.items():
            last_error = self._errors.get(name)
            if last_error is not None and now - last_error < HARDWARE_ERROR_COOLDOWN_SECONDS:
                continue
            self._errors.pop(name, None)
            hardware_futures[self._executor.submit(poll)] = name

        cpu_future = self._executor.submit(get_cpu_stats)
        done, _ = wait([*hardware_futures, cpu_future], timeout=POLL_TIMEOUT_SECONDS)
        gpu_usages: dict[str, dict[str, Any]] = {}

        for future, name in hardware_futures.items():
            if future not in done:
                logger.warning("Timed out collecting %s stats", name)
                self._errors[name] = time.monotonic()
                continue
            try:
                result = future.result()
            except Exception:
                logger.exception("Failed to collect %s stats", name)
                self._errors[name] = time.monotonic()
                continue
            gpu_usages.update(result.gpu)
            if not result.ok:
                self._errors[name] = time.monotonic()

        if gpu_usages:
            all_stats["gpu_usages"] = gpu_usages

        if cpu_future in done:
            try:
                cpu_stats = cpu_future.result()
            except Exception:
                logger.exception("Failed to collect CPU stats")
            else:
                if cpu_stats:
                    all_stats["cpu_usages"] = cpu_stats

    def stop(self) -> None:
        self._config_subscriber.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _scan_ffmpeg(self) -> set[str]:
        names: set[str] = set()
        for camera in self.config.cameras.values():
            values = [camera.ffmpeg.hwaccel_args]
            values.extend(stream.hwaccel_args for stream in camera.ffmpeg.inputs)
            for value in values:
                args = " ".join(value) if isinstance(value, list) else value
                if args and (name := _hwaccel_hardware(args)):
                    names.add(name)
        return names

    def _scan_detectors(self) -> set[str]:
        if "onnx:nvidia" not in _present_hardware():
            return set()

        for model in self.config.models:
            for spec in self.config.devices_for_model(model):
                if spec.detector == "onnx" and spec.device != "CPU":
                    return {"nvidia"}
        return set()

    def _scan_enrichments(self) -> set[str]:
        if "onnx:nvidia" not in _present_hardware():
            return set()

        config = self.config
        if config.semantic_search.enabled and config.semantic_search.device != "CPU":
            return {"nvidia"}
        if config.face_recognition.enabled and config.face_recognition.device != "CPU":
            return {"nvidia"}
        if config.lpr.enabled and config.lpr.device != "CPU":
            return {"nvidia"}

        transcription = config.audio_transcription
        if transcription.device == "GPU" and (
            transcription.enabled
            or any(c.audio_transcription.enabled for c in config.cameras.values())
        ):
            return {"nvidia"}
        return set()
