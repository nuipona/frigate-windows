"""Discover object-detection hardware through native Windows runtimes.

No device nodes, ``/proc`` files, sysfs entries, or container mappings are
consulted. Hardware is visible only when the Windows driver API that will run
inference can see it.
"""

import logging

from pydantic import BaseModel, Field

from frigate.detectors.detector_types import config_types
from frigate.util.platform import get_display_adapters

logger = logging.getLogger(__name__)


class HardwareUnit(BaseModel):
    """One addressable inference device."""

    device: str = Field(
        title="Device string",
        description="Value to put in a model's devices list, for example 'openvino:GPU.0'.",
    )
    label: str = Field(
        title="Unit label",
        description="Human-readable device name shown in the configuration UI.",
    )


class DetectionHardware(BaseModel):
    """One kind of inference hardware and its addressable units."""

    key: str
    detector: str
    name: str
    units: list[HardwareUnit]
    count: int
    unlimited: bool


def _is_shareable(detector: str) -> bool:
    config_class = config_types.get(detector)
    return config_class.shareable if config_class else True


def _hardware(
    key: str, detector: str, name: str, units: list[HardwareUnit]
) -> DetectionHardware:
    return DetectionHardware(
        key=key,
        detector=detector,
        name=name,
        units=units,
        count=len(units),
        unlimited=_is_shareable(detector),
    )


def _openvino_available_devices() -> list[str]:
    """Return devices exposed by the installed Windows OpenVINO runtime."""
    try:
        from openvino import Core

        return list(Core().available_devices)
    except Exception:
        logger.debug("Could not enumerate OpenVINO devices", exc_info=True)
        return []


def _openvino_hardware(kind: str, key: str, name: str) -> DetectionHardware | None:
    devices = [
        device
        for device in _openvino_available_devices()
        if device == kind or device.startswith(f"{kind}.")
    ]
    if not devices:
        return None

    units = [
        HardwareUnit(device=f"openvino:{device}", label=device) for device in devices
    ]
    return _hardware(key, "openvino", name, units)


def detect_intel_gpu() -> DetectionHardware | None:
    """Find Intel GPUs addressable by OpenVINO on Windows."""
    return _openvino_hardware("GPU", "openvino:GPU", "Intel GPU")


def detect_intel_npu() -> DetectionHardware | None:
    """Find Intel NPUs addressable by OpenVINO on Windows."""
    return _openvino_hardware("NPU", "openvino:NPU", "Intel NPU")


def _onnx_providers() -> set[str]:
    try:
        import onnxruntime as ort

        return set(ort.get_available_providers())
    except Exception:
        logger.debug("Could not enumerate ONNX Runtime providers", exc_info=True)
        return set()


def detect_directml_gpu() -> DetectionHardware | None:
    """Find display adapters addressable by ONNX Runtime DirectML."""
    if "DmlExecutionProvider" not in _onnx_providers():
        return None

    adapters = get_display_adapters()
    if not adapters:
        return None

    units = [
        HardwareUnit(device=f"onnx:{index}", label=label)
        for index, label in enumerate(adapters)
    ]
    return _hardware("onnx:directml", "onnx", "DirectML GPU", units)


def detect_nvidia_gpu() -> DetectionHardware | None:
    """Find NVIDIA GPUs when ONNX Runtime's CUDA provider is installed."""
    if "CUDAExecutionProvider" not in _onnx_providers():
        return None

    try:
        import py3nvml.py3nvml as nvml

        nvml.nvmlInit()
        count = nvml.nvmlDeviceGetCount()
        units = []
        for index in range(count):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            label = nvml.nvmlDeviceGetName(handle)
            if isinstance(label, bytes):
                label = label.decode("utf-8", errors="replace")
            units.append(HardwareUnit(device=f"onnx:{index}", label=label))
    except Exception:
        logger.debug("Could not enumerate NVIDIA GPUs through NVML", exc_info=True)
        return None

    if not units:
        return None

    name = units[0].label if len(units) == 1 else "NVIDIA GPU"
    return _hardware("onnx:nvidia", "onnx", name, units)


def detect_cpu() -> DetectionHardware:
    """The CPU fallback, which is always available."""
    return _hardware(
        "cpu",
        "cpu",
        "CPU",
        [HardwareUnit(device="cpu", label="CPU")],
    )


WINDOWS_PROBES = (
    detect_intel_npu,
    detect_intel_gpu,
    detect_nvidia_gpu,
    detect_directml_gpu,
    detect_cpu,
)


class HardwareProber:
    """Probe Windows inference runtimes and cache the result per process."""

    _hardware: list[DetectionHardware] | None = None

    def probe(self, refresh: bool = False) -> list[DetectionHardware]:
        if self._hardware is not None and not refresh:
            return self._hardware

        found = []
        for probe in WINDOWS_PROBES:
            try:
                result = probe()
            except Exception:
                logger.warning("Failed to probe for %s", probe.__name__, exc_info=True)
                continue

            if result is not None:
                found.append(result)

        logger.debug("Detected Windows hardware: %s", [item.key for item in found])
        self._hardware = found
        return found


hardware_prober = HardwareProber()
