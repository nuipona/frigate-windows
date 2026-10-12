"""Resolve the version Frigate reports.

``make version`` generates ``frigate/version.py`` while building the container
image. A native install, including the Windows one, runs from a checkout that
never gets that module, so the version is read from the installed distribution
metadata instead. A checkout that is not installed at all, such as a bare
``git clone`` on a machine whose Python cannot import the package, still gets a
usable string rather than an ImportError that takes the whole application down
before it can log anything.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

try:
    from frigate.version import VERSION
except ModuleNotFoundError:
    try:
        VERSION = distribution_version("frigate-nvr")
    except PackageNotFoundError:
        VERSION = "dev"

__all__ = ["VERSION"]
