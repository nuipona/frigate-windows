"""Parse detector device strings for the native Windows runtime."""

import logging
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from frigate.detectors.detector_config import BaseDetectorConfig, ModelConfig
from frigate.detectors.detector_types import (
    DetectorConfig,
    config_types,
    failed_plugin_imports,
)

logger = logging.getLogger(__name__)

_detector_adapter: TypeAdapter[BaseDetectorConfig] = TypeAdapter(DetectorConfig)

# Public list used by the API, config validation, and diagnostics.
SUPPORTED_DETECTORS = ("cpu", "onnx", "openvino", "zmq")

_PLUGIN_MODULE_KEYS = {"cpu_tfl": "cpu", "zmq_ipc": "zmq"}


def _import_failure_reason(detector: str) -> str | None:
    """Why a supported detector runtime could not be imported, if known."""
    for module, error in failed_plugin_imports.items():
        if _PLUGIN_MODULE_KEYS.get(module, module) == detector:
            return error

    return None


def _unknown_detector_message(raw: str, detector: str) -> str:
    """Explain an unavailable detector using only the Windows product model."""
    supported = ", ".join(SUPPORTED_DETECTORS)

    if detector in SUPPORTED_DETECTORS:
        failure = _import_failure_reason(detector)
        if failure is not None:
            return (
                f"'{raw}' names the {detector} detector, but its Windows runtime "
                f"could not be loaded: {failure}"
            )

    return (
        f"'{raw}' does not name a supported detector. "
        f"Available detectors are {supported}."
    )


@dataclass(frozen=True)
class DeviceSpec:
    """A parsed ``<detector>`` or ``<detector>:<device>`` string."""

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
    """Parse and validate a native Windows detector device string."""
    detector, separator, device = raw.partition(":")

    if detector not in SUPPORTED_DETECTORS or detector not in config_types:
        raise DeviceParseError(_unknown_detector_message(raw, detector))

    spec = DeviceSpec(raw=raw, detector=detector, device=device if separator else None)

    # Surface a bad device now rather than when the detection process starts.
    build_detector_config(spec, None)
    return spec


def build_detector_config(
    spec: DeviceSpec, model: ModelConfig | None
) -> BaseDetectorConfig:
    """Build the validated detector configuration described by ``spec``."""
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
    """Build unique process names, suffixing repeated shareable devices."""
    names: list[str] = []
    seen: dict[str, int] = {}

    for spec in devices:
        count = seen.get(spec.raw, 0) + 1
        seen[spec.raw] = count
        names.append(spec.raw if count == 1 else f"{spec.raw}#{count}")

    return names
