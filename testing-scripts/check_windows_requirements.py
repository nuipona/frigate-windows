"""Check that the native Windows dependency set can actually install.

Two things are verified, both against the PyPI JSON API so the check needs no
Windows machine:

1. Every requirement resolves to a version and publishes a Windows wheel (or is
   pure Python and installs from its source distribution).
2. Every module Frigate imports at module scope that is not in the standard
   library is provided by a requirement, either directly or as a dependency of
   one. A package that arrives only transitively today can disappear with an
   upstream release, and the failure shows up as an ImportError on a user's
   machine rather than in CI.

Run from the repository root::

    python testing-scripts/check_windows_requirements.py

Exits non-zero and prints what is missing when the check fails.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO_ROOT / "requirements.txt"
PACKAGE_ROOT = REPO_ROOT / "frigate"

#: import name -> distribution name, for the packages where they differ
IMPORT_TO_DISTRIBUTION = {
    "PIL": "Pillow",
    "cv2": "opencv-contrib-python",
    "dateutil": "python-dateutil",
    "paho": "paho-mqtt",
    "peewee_migrate": "peewee-migrate",
    "playhouse": "peewee",
    "py3nvml": "py3nvml",
    "py_vapid": "py-vapid",
    "ruamel": "ruamel.yaml",
    "sherpa_onnx": "sherpa-onnx",
    "sqlite_vec": "sqlite-vec",
    "titlecase": "titlecase",
    "zeep": "zeep",
    "zmq": "pyzmq",
    "ai_edge_litert": "ai-edge-litert",
    "bs4": "beautifulsoup4",
    "google": "google-genai",
    "onvif": "onvif-zeep-async",
    "pydantic_settings": "pydantic-settings",
    "tensorflow": "tensorflow-cpu",
    # the pywin32 package provides the SCM modules the Windows service imports
    "servicemanager": "pywin32",
    "win32event": "pywin32",
    "win32service": "pywin32",
    "win32serviceutil": "pywin32",
}

#: packages that publish only a source distribution. They are pure Python, so
#: pip builds them on Windows as part of the install.
SDIST_ONLY = {"titlecase"}

#: modules that are Linux, macOS or accelerator specific and are imported only
#: inside a guarded block or a platform branch
PLATFORM_ONLY = {
    "axengine",
    "cuda",
    "dx_engine",
    "hailo_platform",
    "memray",
    "memryx",
    "mlx",
    "mlx_whisper",
    "mosestokenizer",
    "rknn",
    "rknnlite",
    "setproctitle",
    "silero_vad_iterator",
    "synap",
    "tensorrt",
    "tflite_runtime",
    "tokenize_uk",
    "torch",
    "whisper",
    "whisper_timestamped",
    "wtpsplit",
}

WHEEL_PLATFORM_TAGS = ("win32", "win_amd64", "win_arm64")


def normalize(name: str) -> str:
    """Normalize a distribution name the way PyPI does."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path) -> list[tuple[str, str | None, str]]:
    """Return (distribution, version prefix, raw line) for each requirement."""
    requirements = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()

        if not line or line.startswith("-") or "://" in line:
            continue

        match = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*(.*)$", line)
        name = match.group(1)
        rest = match.group(2).strip()

        if "[" in name:
            name = name.split("[", 1)[0]

        version = None
        pinned = re.match(r"^==\s*([0-9]+(?:\.[0-9]+)*)", rest)

        if pinned:
            version = pinned.group(1)

        requirements.append((name, version, raw.strip()))

    return requirements


def fetch(name: str) -> dict:
    """Fetch the PyPI metadata for a distribution."""
    url = f"https://pypi.org/pypi/{name}/json"

    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def has_windows_build(name: str, version: str | None, metadata: dict) -> bool:
    """Whether a distribution provides a Windows wheel or a pure Python wheel."""
    releases = metadata["releases"]

    if version:
        matching = sorted(release for release in releases if release.startswith(version))
    else:
        matching = sorted(releases)

    if not matching:
        return False

    for release in matching:
        for file in releases[release]:
            if file["packagetype"] != "bdist_wheel":
                continue

            filename = file["filename"]

            if filename.endswith("-none-any.whl"):
                return True

            if any(tag in filename for tag in WHEEL_PLATFORM_TAGS):
                return True

    return False


def imported_distribution(module: str) -> str:
    """Distribution name that provides an import name."""
    return IMPORT_TO_DISTRIBUTION.get(module, module)


def third_party_imports() -> dict[str, set[str]]:
    """Every non standard library module imported at module scope in frigate/."""
    stdlib = sys.stdlib_module_names
    imports: dict[str, set[str]] = {}

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as err:
            print(f"unable to parse {path}: {err}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] if not node.level else []
            else:
                continue

            for name in names:
                top_level = name.split(".")[0]

                if not top_level or top_level in stdlib or top_level == "frigate":
                    continue

                imports.setdefault(top_level, set()).add(str(path.relative_to(REPO_ROOT)))

    return imports


def main(requirements_path: Path = REQUIREMENTS) -> int:
    failures: list[str] = []
    requirements = parse_requirements(requirements_path)
    provided: set[str] = set()

    print(f"Checking {requirements_path.relative_to(REPO_ROOT)} against PyPI")

    for name, version, raw in requirements:
        try:
            metadata = fetch(name)
        except (urllib.error.URLError, urllib.error.HTTPError) as err:
            failures.append(f"{raw}: unable to read PyPI metadata ({err})")
            continue

        if normalize(name) not in SDIST_ONLY and not has_windows_build(
            name, version, metadata
        ):
            failures.append(f"{raw}: no Windows wheel or pure Python wheel found")

        provided.add(normalize(name))

        # a requirement's own dependencies can satisfy an import too
        for requirement in metadata["info"].get("requires_dist") or []:
            dependency = requirement.split(";")[0].strip()
            dependency = re.split(r"[\s<>=!~\[]", dependency)[0]

            if dependency:
                provided.add(normalize(dependency))

    print(f"{len(requirements)} requirements checked")

    missing = []

    for module, locations in sorted(third_party_imports().items()):
        if module in PLATFORM_ONLY:
            continue

        distribution = normalize(imported_distribution(module))

        if distribution not in provided:
            missing.append((module, sorted(locations)[0]))

    if missing:
        failures.append(
            "modules imported at module scope with no Windows requirement:\n"
            + "\n".join(f"  {module} (e.g. {path})" for module, path in missing)
        )

    if failures:
        print()
        for failure in failures:
            print(f"FAIL {failure}")
        return 1

    print("Windows requirements are complete and installable")
    return 0


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else REQUIREMENTS
    sys.exit(main(path))
