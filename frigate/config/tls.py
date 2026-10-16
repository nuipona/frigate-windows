import os

from pydantic import Field

from frigate.util.platform import get_config_dir

from .base import FrigateBaseModel

__all__ = ["TlsConfig"]


def _default_cert_file() -> str:
    return os.path.join(get_config_dir(), "tls", "fullchain.pem")


def _default_key_file() -> str:
    return os.path.join(get_config_dir(), "tls", "privkey.pem")


class TlsConfig(FrigateBaseModel):
    enabled: bool = Field(
        default=True,
        title="Enable TLS",
        description=(
            "Enable TLS for Frigate's authenticated public Windows web listener."
        ),
    )
    cert_file: str = Field(
        default_factory=_default_cert_file,
        title="Certificate file",
        description=(
            "PEM certificate chain for the public listener. A self-signed "
            "certificate is generated here when both TLS files are absent."
        ),
    )
    key_file: str = Field(
        default_factory=_default_key_file,
        title="Private key file",
        description="Unencrypted PEM private key for the public listener.",
    )
