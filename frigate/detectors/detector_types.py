import importlib
import logging
import pkgutil
from enum import Enum
from typing import Annotated, Union, get_args

from pydantic import Field

from . import plugins
from .detection_api import DetectionApi
from .detector_config import BaseDetectorConfig

logger = logging.getLogger(__name__)


_included_modules = pkgutil.iter_modules(plugins.__path__, plugins.__name__ + ".")

plugin_modules = []

# detector key -> the import error that stopped its plugin module loading.
# Populated below so a device string for a runtime that is not installed can
# be reported with the real reason instead of "does not name a known detector",
# which is what a missing plugin module looks like downstream.
failed_plugin_imports: dict[str, str] = {}

for _, name, _ in _included_modules:
    try:
        # currently openvino may fail when importing
        # on an arm device with 64 KiB page size.
        plugin_modules.append(importlib.import_module(name))
    except ImportError as e:
        logger.error(f"Error importing detector runtime: {e}")
        module_name = name.rpartition(".")[2]
        failed_plugin_imports[module_name] = str(e)


api_types = {det.type_key: det for det in DetectionApi.__subclasses__()}


class StrEnum(str, Enum):
    pass


DetectorTypeEnum = StrEnum("DetectorTypeEnum", {k: k for k in api_types})

DetectorConfig = Annotated[
    Union[tuple(BaseDetectorConfig.__subclasses__())],  # noqa: UP007
    Field(discriminator="type"),
]


def _discriminator_value(config_class: type[BaseDetectorConfig]) -> str | None:
    """Read the Literal value of a detector config class' type field."""
    field = config_class.model_fields.get("type")

    if field is None:
        return None

    values = get_args(field.annotation)
    return values[0] if values else None


config_types: dict[str, type[BaseDetectorConfig]] = {
    key: config_class
    for config_class in BaseDetectorConfig.__subclasses__()
    if (key := _discriminator_value(config_class)) is not None
}
