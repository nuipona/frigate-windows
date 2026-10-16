from pydantic import Field, field_validator, model_validator

from frigate.util.env import EnvString

from .base import FrigateBaseModel

__all__ = ["ProxyConfig", "HeaderMappingConfig"]


class HeaderMappingConfig(FrigateBaseModel):
    user: str | None = Field(
        default=None,
        pattern=r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$",
        title="User header",
        description="Header containing the authenticated username provided by the upstream proxy.",
    )
    role: str | None = Field(
        default=None,
        pattern=r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$",
        title="Role header",
        description="Header containing the authenticated user's role or groups from the upstream proxy.",
    )
    role_map: dict[str, list[str]] | None = Field(
        default_factory=dict,
        title=("Role mapping"),
        description="Map upstream group values to Frigate roles (for example map admin groups to the admin role).",
    )


class ProxyConfig(FrigateBaseModel):
    header_map: HeaderMappingConfig = Field(
        default_factory=HeaderMappingConfig,
        title="Header mapping",
        description="Map incoming proxy headers to Frigate user and role fields for proxy-based auth.",
    )
    logout_url: str | None = Field(
        default=None,
        title="Logout URL",
        description="URL to redirect users to when logging out via the proxy.",
    )
    auth_secret: EnvString | None = Field(
        default=None,
        title="Proxy secret",
        description=(
            "Secret checked against X-Proxy-Secret. Required when proxy "
            "identity or role headers are mapped."
        ),
    )
    default_role: str | None = Field(
        default="viewer",
        title="Default role",
        description="Default role assigned to proxy-authenticated users when no role mapping applies. Set to 'none' to deny access to unmapped users.",
    )
    separator: str | None = Field(
        default=",",
        title="Separator character",
        description="Character used to split multiple values provided in proxy headers.",
    )

    @model_validator(mode="after")
    def require_secret_for_identity_headers(self) -> "ProxyConfig":
        if (self.header_map.user or self.header_map.role) and not self.auth_secret:
            raise ValueError(
                "proxy.auth_secret is required when proxy identity headers are mapped"
            )
        return self

    @field_validator("default_role", mode="before")
    @classmethod
    def normalize_deny_sentinel(cls, v):
        # Fail closed on capitalization: an unnormalized "None" would miss the
        # sentinel and fall back to viewer, granting the access it was meant to
        # deny. Other role names stay case-sensitive.
        if isinstance(v, str) and v.strip().lower() == "none":
            return "none"
        return v

    @field_validator("separator", mode="before")
    @classmethod
    def validate_separator_length(cls, v):
        if v is not None and len(v) != 1:
            raise ValueError("Separator must be exactly one character")
        return v
