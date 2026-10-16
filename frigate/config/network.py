from pydantic import Field, model_validator

from .base import FrigateBaseModel

__all__ = ["IPv6Config", "ListenConfig", "NetworkingConfig"]


class IPv6Config(FrigateBaseModel):
    enabled: bool = Field(
        default=False,
        title="Enable IPv6",
        description="Allow an IPv6 address in FRIGATE_WEB_HOST.",
    )


class ListenConfig(FrigateBaseModel):
    internal: int = Field(
        default=5000,
        ge=1,
        le=65535,
        title="Internal port",
        description=(
            "Loopback-only port for Frigate media jobs. Requests on this port "
            "are trusted and never exposed to the LAN."
        ),
    )
    external: int = Field(
        default=8971,
        ge=1,
        le=65535,
        title="External port",
        description="Authenticated Windows web-interface port.",
    )

    @property
    def internal_port(self) -> int:
        return self.internal

    @property
    def external_port(self) -> int:
        return self.external

    @model_validator(mode="after")
    def validate_distinct_ports(self) -> "ListenConfig":
        if self.internal == self.external:
            raise ValueError("internal and external must listen on different ports")

        return self


class NetworkingConfig(FrigateBaseModel):
    ipv6: IPv6Config = Field(
        default_factory=IPv6Config,
        title="IPv6 configuration",
        description="IPv6 support for the native Windows listener.",
    )
    listen: ListenConfig = Field(
        default_factory=ListenConfig,
        title="Listening ports configuration",
        description=(
            "The loopback-only internal port and authenticated public Windows "
            "web port. FRIGATE_WEB_PORT may override the public listener."
        ),
    )
