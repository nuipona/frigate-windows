from pydantic import Field

from .base import FrigateBaseModel

__all__ = ["TelemetryConfig", "StatsConfig"]


class StatsConfig(FrigateBaseModel):
    nvidia_gpu_stats: bool = Field(
        default=True,
        title="NVIDIA GPU stats",
        description="Collect utilization, encoder, decoder, memory, and temperature through NVML when an NVIDIA GPU is present.",
    )


class TelemetryConfig(FrigateBaseModel):
    stats: StatsConfig = Field(
        default_factory=StatsConfig,
        title="System stats",
        description="Native Windows hardware-statistics options.",
    )
    version_check: bool = Field(
        default=True,
        title="Version check",
        description="Enable an outbound check to detect if a newer application version is available.",
    )
