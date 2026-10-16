"""Bootstrap a native Windows Frigate install.

Sets up everything the manual recipe in the installation docs asks for, in
one command run from an ordinary PowerShell window:

    py -3.12 bootstrap_windows.py

The script creates the virtual environment, installs the native Windows
requirements, fetches digest-pinned Windows builds of FFmpeg and go2rtc plus
the CPU detection and YAMNet audio models into the Frigate data tree, builds
the web interface when Node.js is available, and writes a launcher that
restarts Frigate when Frigate asks to be restarted (the UI's restart action
and watchdog both rely
on that).

It uses only the Python standard library so it can run before anything is
installed. Downloads use exact URLs and enforce their vendor-verified SHA-256
digests. `--print-digests` reports any legacy trust-on-first-use entries.
"""

import argparse
import hashlib
import json
import logging
import os
import platform
import shutil
import struct
import subprocess
import sys
import urllib.request
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("bootstrap")

REPO_ROOT = Path(__file__).resolve().parent
REQUIREMENTS = REPO_ROOT / "requirements.txt"
LAUNCHER_NAME = "run_frigate.ps1"
RESTART_MARKER_NAME = "restart.requested"

# onnxruntime and onnxruntime-directml both provide the onnxruntime import,
# so DirectML is a swap rather than an addition. The DirectML package lags
# the CPU package in version; 1.24 is the newest line with a CPython 3.12
# Windows wheel.
ONNXRUNTIME_PACKAGE = "onnxruntime"
DIRECTML_PACKAGE = "onnxruntime-directml==1.24.*"


@dataclass(frozen=True)
class PinnedArtifact:
    """A Windows binary build Frigate can fetch, pinned to one URL.

    `expected_sha256` empty means the digest is recorded on first download
    (trust on first use) and enforced afterwards. Once a maintainer has
    verified a digest it belongs here so every later install enforces it.
    """

    name: str
    version: str
    url: str
    # archive members to extract, by their full path inside the archive
    members: tuple[str, ...]
    # destination directory for the extracted files, relative to the config
    # directory; empty means the config directory itself
    dest_subdir: str = ""
    expected_sha256: str = ""


@dataclass(frozen=True)
class PinnedFile:
    """A single downloaded runtime file with a vendor-verified digest."""

    name: str
    version: str
    url: str
    filename: str
    dest_subdir: str
    expected_sha256: str


FFMPEG_ARTIFACT = PinnedArtifact(
    name="ffmpeg",
    version="8.1.2",
    url=(
        "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/"
        "ffmpeg-8.1.2-essentials_build.zip"
    ),
    members=(
        "ffmpeg-8.1.2-essentials_build/bin/ffmpeg.exe",
        "ffmpeg-8.1.2-essentials_build/bin/ffprobe.exe",
    ),
    dest_subdir="ffmpeg",
    expected_sha256="db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
)

GO2RTC_ARTIFACT = PinnedArtifact(
    name="go2rtc",
    version="1.9.14",
    url=(
        "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.14/go2rtc_win64.zip"
    ),
    members=("go2rtc.exe",),
    expected_sha256="dd4167d75cb04abe618855b7c71f8658bd009f60c1a71835d134d2c11c939907",
)

CPU_DETECTION_MODEL = PinnedFile(
    name="cpu-detection-model",
    version="frogfish",
    url=(
        "https://github.com/google-coral/test_data/raw/release-frogfish/"
        "ssdlite_mobiledet_coco_qat_postprocess.tflite"
    ),
    filename="cpu_model.tflite",
    dest_subdir="model_cache",
    expected_sha256="90bb33a634e041914cc1819aa5df99818e6c396c4d2db952c0fd7a9cffc4724f",
)

AUDIO_MODEL = PinnedFile(
    name="yamnet-audio-model",
    version="1",
    url=(
        "https://tfhub.dev/google/lite-model/yamnet/classification/tflite/1"
        "?lite-format=tflite"
    ),
    filename="cpu_audio_model.tflite",
    dest_subdir="model_cache",
    expected_sha256="10c95ea3eb9a7bb4cb8bddf6feb023250381008177ac162ce169694d05c317de",
)


class BootstrapError(RuntimeError):
    """The bootstrap cannot continue."""


class DigestMismatchError(BootstrapError):
    """A downloaded or cached artifact does not match its pinned digest."""


### Layout


class BaseLayout:
    """The directories of a native install, computed like the app computes them.

    Mirrors frigate/util/platform.py without importing it: this script runs
    before Frigate's dependencies are installed, so the app package may not
    even be importable yet.
    """

    def __init__(self, service_mode: bool = False) -> None:
        if service_mode:
            default_root = Path(os.environ.get("ProgramData", "C:/ProgramData"))
        else:
            default_root = Path(
                os.environ.get("LOCALAPPDATA", "~/AppData/Local")
            ).expanduser()

        self.base_dir = Path(
            os.environ.get("FRIGATE_BASE_DIR") or default_root / "Frigate"
        ).expanduser()
        self.config_dir = Path(
            os.environ.get("FRIGATE_CONFIG_DIR") or self.base_dir / "config"
        )
        self.media_dir = Path(
            os.environ.get("FRIGATE_MEDIA_DIR") or self.base_dir / "media"
        )
        self.cache_dir = Path(
            os.environ.get("FRIGATE_CACHE_DIR") or self.base_dir / "cache"
        )
        self.tmp_dir = Path(os.environ.get("FRIGATE_TMP_DIR") or self.base_dir / "tmp")

    def ensure_dirs(self) -> None:
        for directory in (
            self.base_dir,
            self.config_dir,
            self.media_dir,
            self.cache_dir,
            self.tmp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def restart_marker(self) -> Path:
        return self.tmp_dir / RESTART_MARKER_NAME


### Checksums and extraction


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def load_recorded_digests(digest_store: Path) -> dict[str, str]:
    if digest_store.is_file():
        try:
            with open(digest_store, encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, ValueError):
            return {}

        return data if isinstance(data, dict) else {}

    return {}


def save_recorded_digests(digest_store: Path, digests: dict[str, str]) -> None:
    digest_store.parent.mkdir(parents=True, exist_ok=True)

    with open(digest_store, "w", encoding="utf-8") as file:
        json.dump(digests, file, indent=2, sort_keys=True)
        file.write("\n")


def verify_or_record_digest(
    archive: Path, artifact: PinnedArtifact | PinnedFile, digest_store: Path
) -> str:
    """Enforce the artifact's digest, recording it on first use.

    A pinned digest is always enforced. Without one, the first download's
    digest is recorded and every later run verifies against it, so an
    artifact that changes under a pinned URL is caught on the second
    install onwards.
    """
    digest = sha256_file(archive)

    if artifact.expected_sha256:
        if digest != artifact.expected_sha256:
            raise DigestMismatchError(
                f"{artifact.name} does not match the pinned sha256: got "
                f"{digest}, expected {artifact.expected_sha256}. Delete "
                f"{archive} and retry, and if the upstream build changed, "
                "update the pin in bootstrap_windows.py"
            )

        return digest

    recorded = load_recorded_digests(digest_store)

    if artifact.name in recorded:
        if recorded[artifact.name] != digest:
            raise DigestMismatchError(
                f"{artifact.name} does not match the sha256 recorded on this "
                f"machine: got {digest}, expected {recorded[artifact.name]}. "
                f"Delete {digest_store} to accept a new build, and consider "
                "pinning the new digest in bootstrap_windows.py"
            )
    else:
        recorded[artifact.name] = digest
        save_recorded_digests(digest_store, recorded)
        logger.warning(
            "%s: recorded first-use sha256 %s. Verify it against the vendor "
            "for the URL in bootstrap_windows.py and pin it there",
            artifact.name,
            digest,
        )

    return digest


def extract_members(
    archive: Path, members: tuple[str, ...], dest_dir: Path
) -> list[Path]:
    """Extract exactly the named members of a zip archive into dest_dir.

    The extracted file keeps only its basename: the ffmpeg zip nests its
    binaries a couple of directories deep and the destination layout should
    not depend on the build's internal directory name.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()

        for member in members:
            if member not in names:
                raise BootstrapError(
                    f"{archive.name} does not contain {member}; the build's "
                    "layout changed and the pin in bootstrap_windows.py needs "
                    "updating"
                )

            target = dest_dir / Path(member).name
            with bundle.open(member) as source, open(target, "wb") as out:
                out.write(source.read())

            # the executable bit does not exist on Windows, but extracting on
            # a POSIX host (tests, CI) should still produce a usable file
            target.chmod(target.stat().st_mode | 0o111)
            extracted.append(target)

    return extracted


def download(url: str, dest: Path) -> None:
    """Stream an HTTPS response into place atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s", url)
    partial = dest.with_name(f"{dest.name}.part")

    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(
            partial, "wb"
        ) as out:
            while True:
                chunk = response.read(1024 * 1024)

                if not chunk:
                    break

                out.write(chunk)
        os.replace(partial, dest)
    finally:
        try:
            partial.unlink()
        except OSError:
            pass


def install_artifact(
    artifact: PinnedArtifact, layout: BaseLayout, download_dir: Path
) -> list[Path]:
    """Make sure the artifact's binaries are present and verified.

    Existing binaries are left alone so an offline or manually installed
    build keeps working; the digest bookkeeping only applies to archives
    this script fetches.
    """
    dest_dir = (
        layout.config_dir / artifact.dest_subdir
        if artifact.dest_subdir
        else layout.config_dir
    )
    existing = [dest_dir / Path(member).name for member in artifact.members]

    if all(path.is_file() for path in existing):
        logger.info("%s already installed in %s", artifact.name, dest_dir)
        return existing

    archive = download_dir / f"{artifact.name}-{artifact.version}.zip"
    digest_store = download_dir / "digests.json"

    if not archive.is_file():
        download(artifact.url, archive)
    else:
        logger.info("Using cached %s", archive)

    digest = verify_or_record_digest(archive, artifact, digest_store)
    logger.info("%s sha256 %s", artifact.name, digest)

    installed = extract_members(archive, artifact.members, dest_dir)
    logger.info(
        "Installed %s to %s", ", ".join(path.name for path in installed), dest_dir
    )
    return installed


def install_file(
    artifact: PinnedFile, layout: BaseLayout, download_dir: Path
) -> Path:
    """Install one digest-pinned model or data file into the config tree."""
    dest_dir = layout.config_dir / artifact.dest_subdir
    target = dest_dir / artifact.filename

    if target.is_file() and sha256_file(target) == artifact.expected_sha256:
        logger.info("%s already installed at %s", artifact.name, target)
        return target

    if target.exists():
        logger.warning("Replacing %s because its sha256 is not the pinned value", target)

    cached = download_dir / f"{artifact.name}-{artifact.version}-{artifact.filename}"
    digest_store = download_dir / "digests.json"
    if not cached.is_file():
        download(artifact.url, cached)
    else:
        logger.info("Using cached %s", cached)

    digest = verify_or_record_digest(cached, artifact, digest_store)
    logger.info("%s sha256 %s", artifact.name, digest)

    dest_dir.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    try:
        shutil.copyfile(cached, temporary)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass

    logger.info("Installed %s to %s", artifact.name, target)
    return target


### Virtual environment and dependencies


def python_executable_in(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"

    return venv_dir / "bin" / "python"


def check_interpreter() -> None:
    """Require the only supported runtime: 64-bit CPython 3.12 on Windows."""
    if os.name != "nt":
        raise BootstrapError(
            f"This installer runs only on native Windows; detected {sys.platform!r}."
        )

    if platform.python_implementation() != "CPython":
        raise BootstrapError("CPython 3.12 is required.")

    # unpacked so mypy does not fold the comparison against its configured
    # python_version and declare the rest of the function unreachable
    major, minor = sys.version_info[:2]

    if (major, minor) != (3, 12):
        raise BootstrapError(
            f"Python 3.12 is required, this is {platform.python_version()}. "
            "Install it from https://www.python.org/downloads/ and run this "
            "script with `py -3.12 bootstrap_windows.py`"
        )

    pointer_size = struct.calcsize("P")

    if pointer_size != 8:
        raise BootstrapError(
            "64-bit Python is required; the Windows wheels Frigate installs "
            "do not exist for 32-bit Python"
        )


def ensure_venv(venv_dir: Path) -> Path:
    python = python_executable_in(venv_dir)

    if python.is_file():
        logger.info("Using the existing virtual environment at %s", venv_dir)
        return python

    logger.info("Creating the virtual environment at %s", venv_dir)
    venv.create(str(venv_dir), with_pip=True)
    return python


def run(command: list[str], *, cwd: Path | None = None) -> None:
    logger.info("Running %s", " ".join(command))
    result = subprocess.run(command, cwd=cwd)

    if result.returncode != 0:
        raise BootstrapError(
            f"Command failed with exit code {result.returncode}: " + " ".join(command)
        )


def install_requirements(python: Path, directml: bool) -> None:
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
    run(
        [str(python), "-m", "pip", "install", "--editable", str(REPO_ROOT), "--no-deps"]
    )

    if directml:
        logger.info(
            "Swapping %s for %s for GPU inference through DirectML",
            ONNXRUNTIME_PACKAGE,
            DIRECTML_PACKAGE,
        )
        run([str(python), "-m", "pip", "uninstall", "-y", ONNXRUNTIME_PACKAGE])
        run([str(python), "-m", "pip", "install", DIRECTML_PACKAGE])


### Web interface


def build_web() -> bool:
    """Build the web interface, returning whether it was built."""
    npm = shutil.which("npm")

    if not npm:
        logger.warning(
            "Node.js is not on PATH, so the web interface was not built and "
            "will not be served. Install Node.js 22 LTS from "
            "https://nodejs.org/ and run `npm ci` and `npm run build:native` "
            "in the web directory, or use a release bundle that ships the "
            "built interface"
        )
        return False

    web_dir = REPO_ROOT / "web"
    run([npm, "ci"], cwd=web_dir)
    run([npm, "run", "build:native"], cwd=web_dir)
    return True


### Launcher and shortcut


def powershell_literal(value: str | Path) -> str:
    """Quote one value as a PowerShell single-quoted string."""
    return "'" + str(value).replace("'", "''") + "'"


def build_launcher_script(venv_dir: Path, layout: BaseLayout) -> str:
    """The PowerShell launcher that keeps Frigate restartable.

    Frigate signals a restart by writing a marker file (see
    frigate/util/services.py). The launcher starts Frigate again only when
    the marker is there, so Ctrl+C and closing the console still stop it.
    """
    python = python_executable_in(venv_dir).resolve()
    configured_paths = {
        "FRIGATE_BASE_DIR": layout.base_dir,
        "FRIGATE_CONFIG_DIR": layout.config_dir,
        "FRIGATE_MEDIA_DIR": layout.media_dir,
        "FRIGATE_CACHE_DIR": layout.cache_dir,
        "FRIGATE_TMP_DIR": layout.tmp_dir,
    }
    environment = "\n".join(
        f"if (-not $env:{name}) {{ $env:{name} = {powershell_literal(path)} }}"
        for name, path in configured_paths.items()
    )
    return f"""# Generated by bootstrap_windows.py. Start Frigate with this script.
$ErrorActionPreference = "Stop"
$python = {powershell_literal(python)}
{environment}
$marker = Join-Path $env:FRIGATE_TMP_DIR "{RESTART_MARKER_NAME}"

while ($true) {{
    & $python -m frigate @args
    $code = $LASTEXITCODE

    if (Test-Path $marker) {{
        Remove-Item $marker -ErrorAction SilentlyContinue
        Write-Host "Frigate requested a restart, starting it again"
        continue
    }}

    if ($code -ne 0) {{
        Write-Host "Frigate exited with code $code"
    }}

    break
}}
"""


def install_launcher(venv_dir: Path, layout: BaseLayout) -> Path:
    path = REPO_ROOT / LAUNCHER_NAME
    path.write_text(build_launcher_script(venv_dir, layout), encoding="utf-8")
    logger.info("Wrote the launcher to %s", path)
    return path


def install_start_menu_shortcut(launcher: Path) -> bool:
    """Create a Start Menu shortcut, best effort, Windows only."""
    if os.name != "nt":
        logger.debug("Start menu shortcuts are a Windows feature")
        return False

    programs = Path(os.environ.get("APPDATA", "")) / (
        "Microsoft/Windows/Start Menu/Programs"
    )
    target = programs / "Frigate.lnk"
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{target}'); "
        f"$shortcut.TargetPath = 'powershell.exe'; "
        f"$shortcut.Arguments = '-ExecutionPolicy Bypass -File {launcher}'; "
        f"$shortcut.WorkingDirectory = '{REPO_ROOT}'; "
        "$shortcut.Save()"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
        )
    except OSError as err:
        logger.warning("Unable to create the Start Menu shortcut: %s", err)
        return False

    if result.returncode != 0:
        logger.warning(
            "Unable to create the Start Menu shortcut: %s", result.stderr.strip()
        )
        return False

    logger.info("Created the Start Menu shortcut at %s", target)
    return True


def add_defender_exclusion(layout: BaseLayout) -> None:
    """Ask Defender to skip the Frigate directories.

    Needs an elevated PowerShell; a normal one fails with a clear message
    rather than silently doing nothing.
    """
    if os.name != "nt":
        logger.warning("Defender exclusions are a Windows feature")
        return

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Add-MpPreference -ExclusionPath '{layout.base_dir}'",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise BootstrapError(
            "Unable to add the Defender exclusion "
            f"({result.stderr.strip() or result.stdout.strip()}). Run this "
            "script from an elevated PowerShell, or add the exclusion by "
            f"hand: Add-MpPreference -ExclusionPath '{layout.base_dir}'"
        )

    logger.info("Added the Defender exclusion for %s", layout.base_dir)


### Service


def install_service(python: Path, layout: BaseLayout) -> None:
    """Register Frigate with the SCM, through the PowerShell installer.

    The installer resolves the service's data directory, pins it in the
    service's own environment and checks that the service account can reach the
    checkout, the interpreter and that directory, because a service does not
    inherit the installing user's environment. It exits non-zero when it
    refuses, so a failed install is reported rather than passed over.
    """
    logger.info("Installing the SCM service (needs an elevated shell)...")
    service_script = REPO_ROOT / "scripts" / "install_frigate_service.ps1"

    if not service_script.exists():
        raise BootstrapError(f"Service install script not found: {service_script}")

    result = subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(service_script),
            "-Action",
            "Install",
            "-PythonPath",
            str(python),
            "-BaseDir",
            str(layout.base_dir),
        ],
        check=False,
    )

    if result.returncode != 0:
        raise BootstrapError(
            "The SCM service was not installed. Run the installer by hand to see "
            f"why (it exited with {result.returncode}):\n"
            f"    powershell -ExecutionPolicy Bypass -File {service_script} "
            "-Action Install"
        )


### Orchestration


def print_digests(download_dir: Path) -> None:
    digests = load_recorded_digests(download_dir / "digests.json")

    if not digests:
        logger.info("No recorded digests yet; nothing has been downloaded")
        return

    for name, digest in sorted(digests.items()):
        print(f"{name} = {digest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bootstrap_windows",
        description="Set up a native Windows Frigate install",
    )
    parser.add_argument(
        "--venv",
        default=str(REPO_ROOT / ".venv"),
        help="virtual environment directory (default: %(default)s)",
    )
    parser.add_argument("--skip-venv", action="store_true", help="reuse the venv as is")
    parser.add_argument(
        "--skip-deps", action="store_true", help="do not pip install the requirements"
    )
    parser.add_argument(
        "--skip-binaries",
        action="store_true",
        help="do not fetch FFmpeg, go2rtc, or the detection and audio models",
    )
    parser.add_argument(
        "--skip-web", action="store_true", help="do not build the web interface"
    )
    parser.add_argument(
        "--skip-shortcut",
        action="store_true",
        help="do not create the Start Menu shortcut",
    )
    parser.add_argument(
        "--directml",
        action="store_true",
        default=True,
        help="use onnxruntime-directml for GPU inference on AMD, Intel and Nvidia (default)",
    )
    parser.add_argument(
        "--no-directml",
        action="store_false",
        dest="directml",
        help="use CPU-only onnxruntime instead of DirectML",
    )
    parser.add_argument(
        "--add-defender-exclusion",
        action="store_true",
        help="exclude the Frigate base directory from Microsoft Defender",
    )
    parser.add_argument(
        "--install-service",
        action="store_true",
        help="install the Frigate SCM service (needs an elevated shell)",
    )
    parser.add_argument(
        "--print-digests",
        action="store_true",
        help="print the recorded artifact digests and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.print_digests:
        print_digests(BaseLayout().cache_dir / "downloads")
        return 0

    try:
        check_interpreter()
        layout = BaseLayout(service_mode=args.install_service)
        layout.ensure_dirs()
        download_dir = layout.cache_dir / "downloads"

        if args.skip_venv:
            python = python_executable_in(Path(args.venv))
        else:
            python = ensure_venv(Path(args.venv))

        if not args.skip_deps:
            install_requirements(python, args.directml)

        if not args.skip_binaries:
            install_artifact(FFMPEG_ARTIFACT, layout, download_dir)
            install_artifact(GO2RTC_ARTIFACT, layout, download_dir)
            install_file(CPU_DETECTION_MODEL, layout, download_dir)
            install_file(AUDIO_MODEL, layout, download_dir)

        if not args.skip_web:
            build_web()

        launcher = install_launcher(Path(args.venv), layout)

        if not args.skip_shortcut:
            install_start_menu_shortcut(launcher)

        if args.install_service:
            install_service(python, layout)

        if args.add_defender_exclusion:
            add_defender_exclusion(layout)
    except BootstrapError as err:
        logger.error("%s", err)
        return 1

    logger.info("")
    logger.info("Frigate is installed. Start it with:")
    logger.info(
        "    powershell -ExecutionPolicy Bypass -File %s", REPO_ROOT / LAUNCHER_NAME
    )
    logger.info("The configuration lives in %s", layout.config_dir)

    if args.install_service:
        logger.info(
            "The service keeps its data outside a user profile; the installer "
            "printed the directory it uses."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
