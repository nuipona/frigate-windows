"""Parsing of detection hardware device strings."""

import logging
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from frigate.detectors.detector_config import BaseDetectorConfig, ModelConfig
from frigate.detectors.detector_types import (
    DetectorConfig,
    config_types,
    failed_plugin_imports,
)
from frigate.util.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_detector_adapter: TypeAdapter[BaseDetectorConfig] = TypeAdapter(DetectorConfig)

# plugin module file name -> detector key, for the modules whose name does not
# already match the key their plugin registers
_PLUGIN_MODULE_KEYS = {
    "cpu_tfl": "cpu",
    "edgetpu_tfl": "edgetpu",
    "zmq_ipc": "zmq",
}

# Detectors the native Windows dependency set can run. Everything else needs
# Linux-only drivers or runtimes that requirements-windows.txt does not carry.
WINDOWS_DETECTORS = ("cpu", "onnx", "openvino", "zmq")

# Detector keys whose drivers or runtimes are Linux-only. Some plugin
# modules still import without their vendor SDK, so these cannot be filtered
# reliably by whether their config type was registered.
LINUX_ONLY_DETECTORS = (
    "axengine",
    "deepx",
    "edgetpu",
    "hailo",
    "memryx",
    "rknn",
    "synaptics",
    "teflon_tfl",
    "tensorrt",
)


def _import_failure_reason(detector: str) -> str | None:
    """Why a detector's runtime is not usable, if its plugin failed to import."""
    for module, error in failed_plugin_imports.items():
        if _PLUGIN_MODULE_KEYS.get(module, module) == detector:
            return error

    return None


def _unknown_detector_message(raw: str, detector: str) -> str:
    """Explain why a device string's detector is not available.

    An unknown detector usually means one of three things, each of which needs
    a different message: the name is misspelled, the detector's runtime failed
    to import, or the detector needs Linux-only drivers on a Windows install.
    """
    installed = ", ".join(sorted(config_types))

    if IS_WINDOWS and detector in LINUX_ONLY_DETECTORS:
        return (
            f"'{raw}' needs drivers or runtimes that are only available on "
            f"Linux, so it cannot run on native Windows. Detectors available "
            f"on Windows are {', '.join(WINDOWS_DETECTORS)}. See "
            "https://docs.frigate.video/frigate/installation"
        )

    failure = _import_failure_reason(detector)

    if failure is not None:
        return (
            f"'{raw}' names the {detector} detector, but its runtime is not "
            f"usable: {failure}"
        )

    if IS_WINDOWS:
        return (
            f"'{raw}' does not name a known detector. Detectors available on "
            f"Windows are {', '.join(WINDOWS_DETECTORS)}"
        )

    return (
        f"'{raw}' does not name a known detector. Available detectors are {installed}"
    )


@dataclass(frozen=True)
class DeviceSpec:
    """A parsed `<detector>` or `<detector>:<device>` string."""

    raw: str
    detector: str
    device: str | None

    @property
    def shareable(self) -> bool:
        """Whether this device may be listed more than once."""
        return config_types[self.detector].shareable


class DeviceParseError(ValueError):
    pass


def parse_device(raw: str) -> DeviceSpec:
    """Parse a device string into its detector type and detector specific device.

    Args:
        raw: The configured device string, for example 'edgetpu:pci:0'

    Returns:
        The parsed spec

    Raises:
        DeviceParseError: If the detector type is unknown or the device is not
            valid for that detector
    """
    detector, separator, device = raw.partition(":")

    # Some Linux-only plugins can import successfully on Windows even though
    # their device drivers and runtime archives cannot run there. Reject them
    # before application startup can install their runtime dependencies.
    if IS_WINDOWS and detector not in WINDOWS_DETECTORS:
        raise DeviceParseError(_unknown_detector_message(raw, detector))

    if detector not in config_types:
        raise DeviceParseError(_unknown_detector_message(raw, detector))

    spec = DeviceSpec(raw=raw, detector=detector, device=device if separator else None)

    # surface a bad device now rather than when the detection process starts
    build_detector_config(spec, None)
    return spec


def build_detector_config(
    spec: DeviceSpec, model: ModelConfig | None
) -> BaseDetectorConfig:
    """Build the detector config a device string describes.

    Args:
        spec: The parsed device spec
        model: The model this detector runs, if it has been resolved yet

    Returns:
        The validated detector config

    Raises:
        DeviceParseError: If the device is not valid for this detector type
    """
    config: dict[str, object] = {"type": spec.detector, "model": model}

    if spec.device is not None:
        config_class = config_types[spec.detector]

        try:
            config[config_class.device_spec_field] = config_class.device_spec_type(
                spec.device
            )
        except ValueError as err:
            raise DeviceParseError(
                f"'{spec.raw}' is not a valid {spec.detector} device: {err}"
            ) from err

    try:
        return _detector_adapter.validate_python(config)
    except ValidationError as err:
        raise DeviceParseError(f"'{spec.raw}' is not a valid device: {err}") from err


def runner_names(devices: list[DeviceSpec]) -> list[str]:
    """Build a unique name for each device, since a shareable device may repeat.

    Args:
        devices: Every device spec across every configured model, in config order

    Returns:
        A name per device, suffixed with '#2', '#3', etc. on repeats
    """
    names: list[str] = []
    seen: dict[str, int] = {}

    for spec in devices:
        count = seen.get(spec.raw, 0) + 1
        seen[spec.raw] = count
        names.append(spec.raw if count == 1 else f"{spec.raw}#{count}")

    return names
