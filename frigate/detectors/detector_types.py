"""Load the detector backends supported by the native Windows application.

Detector discovery is deliberately explicit so startup and the configuration
schema do not depend on unrelated SDKs installed on the machine. The native
Windows build supports exactly these backends:

* ``cpu`` — TensorFlow Lite on the host CPU
* ``onnx`` — ONNX Runtime (CPU, DirectML, or a provider installed by the user)
* ``openvino`` — Intel/AMD CPU and Intel GPU/NPU through OpenVINO
* ``zmq`` — inference delegated to an external ZeroMQ service
"""

import importlib
import logging
from enum import StrEnum
from functools import reduce
from operator import or_
from types import UnionType

from pydantic import BaseModel

from frigate.detectors.detection_api import DetectionApi

logger = logging.getLogger(__name__)

SUPPORTED_DETECTOR_MODULES = ("cpu_tfl", "onnx", "openvino", "zmq_ipc")


class DetectorTypeEnum(StrEnum):
    cpu = "cpu"
    onnx = "onnx"
    openvino = "openvino"
    zmq = "zmq"


api_types: dict[str, type[DetectionApi]] = {}
config_types: dict[str, type[BaseModel]] = {}
failed_plugin_imports: dict[str, str] = {}

for module_name in SUPPORTED_DETECTOR_MODULES:
    try:
        module = importlib.import_module(f"frigate.detectors.plugins.{module_name}")
    except ImportError as err:
        # Keep startup importable so configuration validation can produce a
        # useful detector-specific message instead of failing the entire app.
        failed_plugin_imports[module_name] = str(err)
        logger.warning(
            "Windows detector plugin %s could not be imported: %s", module_name, err
        )
        continue

    for name in dir(module):
        candidate = getattr(module, name)

        try:
            is_config = (
                isinstance(candidate, type)
                and issubclass(candidate, BaseModel)
                and candidate.__module__ == module.__name__
            )
            is_api = (
                isinstance(candidate, type)
                and issubclass(candidate, DetectionApi)
                and candidate.__module__ == module.__name__
            )
        except TypeError:
            continue

        if is_config:
            config_types[module.DETECTOR_KEY] = candidate
        elif is_api:
            api_types[module.DETECTOR_KEY] = candidate


if config_types:
    DetectorConfig: UnionType = reduce(or_, config_types.values())
else:

    class UnavailableDetectorConfig(BaseModel):
        """Sentinel used only when every required detector import failed."""

    DetectorConfig = UnavailableDetectorConfig
