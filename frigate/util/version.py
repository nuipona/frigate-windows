"""Resolve the version reported by the native Windows installation."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

try:
    VERSION = distribution_version("frigate-nvr")
except PackageNotFoundError:
    # Source checkouts used by tests are not necessarily installed as a package.
    VERSION = "dev"

__all__ = ["VERSION"]
