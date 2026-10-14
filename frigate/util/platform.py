"""Platform specific helpers.

Frigate was built for the Linux container image, where ``/config``,
``/media/frigate`` and ``/dev/shm`` are fixed locations and POSIX APIs such as
``os.nice``, ``fcntl.flock`` and the ``forkserver`` start method are always
available. Everything in this module exists so that the rest of the codebase
can keep referring to a single set of constants (see ``frigate.const``) while
the actual values differ between platforms.

The rules this module follows:

- Nothing here may import anything from Frigate outside of this module, because
  ``frigate.const`` imports from it and is imported by nearly everything else.
- No POSIX-only module may be imported at module scope. ``fcntl``, ``pwd``,
  ``resource``, ``termios`` and friends do not exist on Windows and a bare
  import turns into an ImportError at startup.
- Every path is returned as an absolute, OS native path. Code that needs a URL
  or a value to show a user uses the helpers at the bottom of this module.

Directory resolution keeps the container layout on Linux so the Docker image
and the Home Assistant app behave exactly as before. On Windows (and macOS)
everything lives under a single base directory, which lets the whole install
land on whichever drive the user picks::

    <base>/
        config/     config.yml, frigate.db, model_cache/
        media/      recordings/, clips/, exports/
        cache/      ffmpeg segment cache, preview frames
        tmp/        go2rtc.yaml, service logs, runtime flags

Set ``FRIGATE_BASE_DIR`` (for example ``D:\\Frigate``) to move the whole tree,
or one of ``FRIGATE_CONFIG_DIR``, ``FRIGATE_MEDIA_DIR``, ``FRIGATE_CACHE_DIR``
and ``FRIGATE_TMP_DIR`` to override a single directory.
"""

import datetime
import logging
import os
import shlex
import shutil
import sys
import threading
import zlib
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

# operating system

IS_WINDOWS = os.name == "nt"
IS_POSIX = os.name == "posix"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# environment variables

BASE_DIR_VAR = "FRIGATE_BASE_DIR"
CONFIG_DIR_VAR = "FRIGATE_CONFIG_DIR"
MEDIA_DIR_VAR = "FRIGATE_MEDIA_DIR"
CACHE_DIR_VAR = "FRIGATE_CACHE_DIR"
TMP_DIR_VAR = "FRIGATE_TMP_DIR"
SERVICE_STOP_EVENT_VAR = "FRIGATE_SERVICE_STOP_EVENT"
WEB_DIR_VAR = "FRIGATE_WEB_DIR"
BIRDSEYE_PORT_VAR = "FRIGATE_BIRDSEYE_PORT"
FFMPEG_DIR_VAR = "FRIGATE_FFMPEG_DIR"
WEB_SERVER_VAR = "FRIGATE_WEB_SERVER"
IPC_PORT_BASE_VAR = "FRIGATE_IPC_PORT_BASE"
WEB_HOST_VAR = "FRIGATE_WEB_HOST"
WEB_PORT_VAR = "FRIGATE_WEB_PORT"

#: the port the API listens on inside the container, behind nginx
INTERNAL_API_PORT = 5001

#: how the web app and API are served
WEB_SERVER_NATIVE = "native"
WEB_SERVER_NGINX = "nginx"

# the paths baked into the container image. A Linux install that has not opted
# into a base directory keeps using these.
CONTAINER_INSTALL_DIR = "/opt/frigate"
CONTAINER_CONFIG_DIR = "/config"
CONTAINER_MEDIA_DIR = "/media/frigate"
CONTAINER_CACHE_DIR = "/tmp/cache"
CONTAINER_TMP_DIR = "/dev/shm"

# subdirectories of the media root that are served to the browser. The web app
# builds media URLs by finding one of these components in a stored path.
MEDIA_URL_COMPONENTS = ("clips", "recordings", "exports")
CACHE_URL_COMPONENT = "cache"


def _env_path(name: str) -> str | None:
    """Read a path from the environment, expanding ``~`` and user variables."""
    value = os.environ.get(name)

    if not value or not value.strip():
        return None

    return os.path.abspath(os.path.expanduser(os.path.expandvars(value.strip())))


def base_dir_is_overridden() -> bool:
    """Whether the user pinned the install root with ``FRIGATE_BASE_DIR``.

    Not cached: the launcher sets the variable before importing Frigate and
    tests set it per case, so it has to be read live.
    """
    return _env_path(BASE_DIR_VAR) is not None


def get_source_root() -> str:
    """Directory containing the ``frigate`` package and the ``web`` build."""
    return str(Path(__file__).resolve().parents[2])


def get_default_labelmap_path() -> str | None:
    """Find the bundled label map in a container or source checkout."""
    if not IS_WINDOWS and os.path.isfile("/labelmap.txt"):
        return "/labelmap.txt"

    source_labelmap = os.path.join(get_source_root(), "labelmap.txt")
    if os.path.isfile(source_labelmap):
        return source_labelmap

    return None


def get_install_dir() -> str:
    """Directory Frigate is installed in.

    The container image installs to ``/opt/frigate``. Everywhere else this is
    the checkout the running code lives in, which is what the launchers and the
    native web server need in order to find ``web/dist``.
    """
    if os.path.isdir(CONTAINER_INSTALL_DIR):
        return CONTAINER_INSTALL_DIR

    return get_source_root()


def get_base_dir() -> str:
    """Root directory for a native install.

    On Linux without ``FRIGATE_BASE_DIR`` this is the media directory, matching
    the container image where media is the only thing under a single root.
    Windows defaults to ``%LOCALAPPDATA%\\Frigate``, which needs no
    administrator rights and therefore works when Frigate is run as a regular
    application. A user with a dedicated recording drive (``D:\\Frigate``) sets
    ``FRIGATE_BASE_DIR`` and gets the whole tree on that drive.
    """
    override = _env_path(BASE_DIR_VAR)

    if override:
        return override

    if IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA")

        if local_app_data:
            return os.path.join(local_app_data, "Frigate")

        return os.path.join(os.path.expanduser("~"), "Frigate")

    if IS_LINUX:
        return CONTAINER_MEDIA_DIR

    return os.path.join(os.path.expanduser("~"), "Frigate")


def get_config_dir() -> str:
    """Directory holding ``config.yml``, the database and the model cache."""
    override = _env_path(CONFIG_DIR_VAR)

    if override:
        return override

    if IS_LINUX and not base_dir_is_overridden():
        return CONTAINER_CONFIG_DIR

    return os.path.join(get_base_dir(), "config")


def get_media_dir() -> str:
    """Directory recordings, clips and exports are written to."""
    override = _env_path(MEDIA_DIR_VAR)

    if override:
        return override

    if IS_LINUX and not base_dir_is_overridden():
        return CONTAINER_MEDIA_DIR

    return os.path.join(get_base_dir(), "media")


def get_cache_dir() -> str:
    """Directory for the temporary segment cache and preview frames."""
    override = _env_path(CACHE_DIR_VAR)

    if override:
        return override

    if IS_LINUX and not base_dir_is_overridden():
        return CONTAINER_CACHE_DIR

    return os.path.join(get_base_dir(), "cache")


def get_tmp_dir() -> str:
    """Runtime scratch directory.

    This replaces ``/dev/shm`` outside of the container image. Shared memory for
    decoded frames uses ``multiprocessing.shared_memory``, which the operating
    system backs with the page file on Windows, so this directory only holds
    files: the go2rtc config, service logs and runtime flags.
    """
    override = _env_path(TMP_DIR_VAR)

    if override:
        return override

    if IS_LINUX and not base_dir_is_overridden():
        return CONTAINER_TMP_DIR

    return os.path.join(get_base_dir(), "tmp")


def get_log_dir() -> str:
    """Directory the service logs (frigate, go2rtc, web server) are written to."""
    return os.path.join(get_tmp_dir(), "logs")


def get_model_cache_dir() -> str:
    """Directory downloaded detection models are cached in."""
    return os.path.join(get_config_dir(), "model_cache")


#: marker in the Vite entry document, which only exists in a source checkout
VITE_DEV_ENTRY_MARKER = "/src/main.tsx"


def _is_built_web_dir(candidate: str) -> bool:
    """Report whether a directory holds a built web app rather than sources."""
    index = os.path.join(candidate, "index.html")

    if not os.path.isfile(index):
        return False

    try:
        with open(index, encoding="utf-8", errors="replace") as file:
            # the Vite dev entry loads TypeScript sources, a build loads assets
            return VITE_DEV_ENTRY_MARKER not in file.read()
    except OSError:
        return False


def get_web_dir() -> str:
    """Directory containing the built web app.

    The container image copies the Vite build to ``/opt/frigate/web``. In a
    checkout the build output is ``web/dist``, and ``web`` itself is skipped
    because its ``index.html`` loads TypeScript sources that a browser cannot
    run.
    """
    override = _env_path(WEB_DIR_VAR)

    if override:
        return override

    install_dir = get_install_dir()
    candidates = (
        os.path.join(install_dir, "web"),
        os.path.join(install_dir, "web", "dist"),
        os.path.join(get_source_root(), "web", "dist"),
        os.path.join(get_source_root(), "web"),
    )

    for candidate in candidates:
        if _is_built_web_dir(candidate):
            return candidate

    return candidates[0]


def web_dir_has_build() -> bool:
    """Report whether a built web app is present."""
    return _is_built_web_dir(get_web_dir())


def ensure_runtime_dirs() -> None:
    """Create the directories a native install needs before startup."""
    media_dir = get_media_dir()

    for path in (
        get_config_dir(),
        media_dir,
        os.path.join(media_dir, "clips"),
        os.path.join(media_dir, "recordings"),
        os.path.join(media_dir, "exports"),
        get_cache_dir(),
        get_tmp_dir(),
        get_log_dir(),
        get_model_cache_dir(),
    ):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as err:
            logger.warning("Unable to create %s: %s", path, err)


# ---------------------------------------------------------------------------
# web server
# ---------------------------------------------------------------------------


def get_web_server_mode() -> str:
    """How the web app and API are served.

    The container image puts nginx in front of Frigate and serves the built web
    app, the media directories and the vod module from there. A native install
    has no nginx, so Frigate serves all of it itself (see ``frigate.api.web``).

    Set ``FRIGATE_WEB_SERVER`` to ``nginx`` or ``native`` to override.
    """
    override = os.environ.get(WEB_SERVER_VAR, "").strip().lower()

    if override in (WEB_SERVER_NATIVE, WEB_SERVER_NGINX):
        return override

    if IS_LINUX and os.path.isdir(CONTAINER_INSTALL_DIR):
        # the container image always runs behind nginx
        return WEB_SERVER_NGINX

    return WEB_SERVER_NATIVE


def serves_web_app() -> bool:
    """Whether Frigate serves the web app itself rather than through nginx."""
    return get_web_server_mode() == WEB_SERVER_NATIVE


def get_web_host() -> str:
    """Interface the web server binds to.

    All interfaces by default, because a network video recorder that only
    listens on localhost is not much use. ``FRIGATE_WEB_HOST=127.0.0.1``
    restricts it to this computer.
    """
    return os.environ.get(WEB_HOST_VAR, "").strip() or "0.0.0.0"


def get_web_port(internal_port: int) -> int:
    """Port the web app and API are served on.

    Defaults to the internal port from the config so the URLs people already
    use keep working, and so internal consumers (motion search, export, debug
    replay) can still fetch vod playlists over loopback.
    """
    override = os.environ.get(WEB_PORT_VAR, "").strip()

    if override.isdigit() and 1 <= int(override) <= 65535:
        return int(override)

    return internal_port


#: storage keys the web UI looks up. They stay the same on every platform so a
#: browser does not need to know where recordings live.
STORAGE_KEYS = {
    "recordings": f"{CONTAINER_MEDIA_DIR}/recordings",
    "clips": f"{CONTAINER_MEDIA_DIR}/clips",
    "cache": CONTAINER_CACHE_DIR,
    "shm": CONTAINER_TMP_DIR,
}


def storage_stat_key(name: str, path: str | None = None) -> str:
    """Key used for a storage entry in the stats payload.

    The web UI indexes into these by name, so the key is the container path
    even when the files on disk live somewhere else entirely.
    """
    return STORAGE_KEYS.get(name, path or name)


def ffmpeg_stdin_input() -> str:
    """The ffmpeg input name for data piped in on standard input.

    ``/dev/stdin`` does not exist on Windows, where ffmpeg uses the explicit
    pipe protocol instead.
    """
    return "pipe:0" if IS_WINDOWS else "/dev/stdin"


def ffmpeg_concat_file_line(path: str) -> str:
    """A ``file '...'`` line for the ffmpeg concat demuxer.

    Paths are written with forward slashes on Windows because the demuxer
    treats a backslash as an escape character inside the quoted path.
    """
    normalized = str(path).replace("\\", "/") if IS_WINDOWS else str(path)

    # the demuxer parses single quotes, so a quote in a file name is escaped
    normalized = normalized.replace("'", "'\\''")

    return f"file '{normalized}'"


def _split_windows_command(command: str) -> list[str]:
    """Split a command line the way the C runtime parses one.

    Windows has no ``shlex``: a process is handed a single string and
    ``CommandLineToArgvW`` splits it, with ``\\`` and ``"`` combining the way
    a Windows path needs them to. Doing the same thing here is what makes a
    value that went through :func:`quote_ffmpeg_arg` come back unchanged, and
    keeps the separators in an unquoted Windows path, which POSIX escape
    handling removes.

    Args:
        command: The command line to split

    Returns:
        The arguments ffmpeg would receive
    """
    args: list[str] = []
    current: list[str] = []
    has_argument = False
    quoted = False
    index = 0
    length = len(command)

    while index < length:
        char = command[index]

        if char in " \t" and not quoted:
            if has_argument:
                args.append("".join(current))
                current = []
                has_argument = False

            index += 1
            continue

        if char == "\\":
            start = index

            while index < length and command[index] == "\\":
                index += 1

            backslashes = index - start

            # a backslash only escapes in front of a quote: 2n backslashes and
            # a quote are n backslashes and a quote delimiter, 2n+1 are n
            # backslashes and a literal quote
            if index < length and command[index] == '"':
                current.append("\\" * (backslashes // 2))

                if backslashes % 2:
                    current.append('"')
                else:
                    quoted = not quoted

                index += 1
            else:
                current.append("\\" * backslashes)

            has_argument = True
            continue

        if char == '"':
            # "" inside a quoted argument is a literal quote
            if quoted and command[index : index + 2] == '""':
                current.append('"')
                index += 2
            else:
                quoted = not quoted
                index += 1

            has_argument = True
            continue

        current.append(char)
        has_argument = True
        index += 1

    if has_argument:
        args.append("".join(current))

    return args


def quote_ffmpeg_arg(value: str) -> str:
    """Quote one argument embedded in a command string later split again.

    POSIX quoting uses ``shlex``. Windows uses the quote and backslash rules
    the C runtime applies to a process command line, so the value survives
    :func:`split_ffmpeg_command` and reaches ffmpeg as one argument.
    """
    if IS_WINDOWS:
        import subprocess

        return subprocess.list2cmdline([str(value)])

    return shlex.quote(str(value))


def split_ffmpeg_command(command: str) -> list[str]:
    """Split an FFmpeg command string while preserving quoted arguments.

    POSIX rules are used everywhere but Windows, where ``shlex`` would treat a
    backslash as an escape character and ``-i D:\\videos\\cam.mp4`` would lose
    its separators and become ``D:videoscam.mp4``.
    """
    if IS_WINDOWS:
        return _split_windows_command(command)

    return shlex.split(command, posix=True)


def get_service_log_path(service: str) -> str:
    """Path of the file the log viewer in the web UI reads for a service.

    Inside the container s6-log writes one file per service under
    ``/dev/shm/logs``. A native install has no s6, so Frigate writes its own
    log file and the launcher redirects go2rtc's output to another.
    """
    if serves_web_app():
        return os.path.join(get_log_dir(), service, "current")

    return f"{CONTAINER_TMP_DIR}/logs/{service}/current"


def get_internal_api_base(internal_port: int) -> str:
    """Base URL internal subprocesses use to reach Frigate's own endpoints.

    Those subprocesses are ffmpeg instances fetching HLS playlists, so they
    need a URL rather than an in process call. In the container this is nginx
    on the internal port; a native install uses its loopback only listener,
    which is always treated as internal and therefore never needs a session.
    """
    if serves_web_app():
        return f"http://127.0.0.1:{INTERNAL_API_PORT}"

    return f"http://127.0.0.1:{internal_port}"


# ---------------------------------------------------------------------------
# multiprocessing
# ---------------------------------------------------------------------------


def get_mp_start_method() -> str:
    """Start method to use for multiprocessing.

    ``forkserver`` is the right choice on Linux because it avoids copying the
    parent's already large heap into every camera and detector process, and
    because it keeps the preload list working. Windows has no ``fork``, so
    ``spawn`` is the only option and ``set_forkserver_preload`` is a no-op.
    """
    if IS_WINDOWS or IS_MACOS:
        return "spawn"

    return "forkserver"


def supports_forkserver() -> bool:
    """Report whether ``forkserver`` can be selected on this platform."""
    return get_mp_start_method() == "forkserver"


def get_shared_memory_total_mb() -> float | None:
    """Approximate size of the shared memory backing store in megabytes.

    Returns the size of ``/dev/shm`` on Linux and ``None`` on platforms where
    shared memory is backed by the page file, because there is no separate
    limit to report or to warn the user about.
    """
    if IS_WINDOWS or IS_MACOS:
        return None

    try:
        return round(shutil.disk_usage(CONTAINER_TMP_DIR).total / pow(2, 20), 1)
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# binaries
# ---------------------------------------------------------------------------


#: suffix appended to binary names on this platform
EXECUTABLE_SUFFIX = ".exe" if IS_WINDOWS else ""


def executable_name(name: str) -> str:
    """Append the platform executable suffix to a binary name."""
    if IS_WINDOWS and not name.lower().endswith(".exe"):
        return f"{name}.exe"

    return name


def _path_candidates() -> list[str]:
    """Places to look for a binary that is not on ``PATH``."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", "")
    install_dir = get_install_dir()

    return [
        os.path.join(install_dir, "bin"),
        os.path.join(install_dir, "ffmpeg", "bin"),
        os.path.join(get_config_dir(), "ffmpeg", "bin"),
        os.path.join(get_ffmpeg_dir(), "bin"),
        # winget, chocolatey and scoop put shims in known locations
        os.path.join(local_app_data, "Microsoft", "WinGet", "Links"),
        os.path.join("C:\\", "ProgramData", "chocolatey", "bin"),
        os.path.join(user_profile, "scoop", "shims"),
        os.path.join("C:\\", "ffmpeg", "bin"),
    ]


def find_executable(name: str, extra_dirs: tuple[str, ...] | None = None) -> str | None:
    """Locate a binary by name.

    Checks ``PATH`` first, then the install directory, a bundled ffmpeg
    directory next to the config, and the package manager shim directories
    Windows users are most likely to have.
    """
    binary = executable_name(name)

    found = shutil.which(binary) or shutil.which(name)

    if found:
        return found

    for directory in tuple(extra_dirs or ()) + tuple(_path_candidates()):
        if not directory:
            continue

        candidate = os.path.join(directory, binary)

        if os.path.isfile(candidate):
            return candidate

    return None


def get_ffmpeg_dir() -> str:
    """Directory holding the ffmpeg build Frigate uses.

    The container image ships several builds under ``/usr/lib/ffmpeg``. Outside
    of the image this points at a directory the user can drop a build into,
    which keeps a bundled ffmpeg separate from anything else on ``PATH``.
    """
    override = _env_path(FFMPEG_DIR_VAR)

    if override:
        return override

    if IS_LINUX:
        # the container image installs its ffmpeg builds here, and a native
        # Linux install is expected to set ffmpeg.path in its config
        return "/usr/lib/ffmpeg"

    bundled = os.path.join(get_config_dir(), "ffmpeg")

    if os.path.isdir(bundled):
        return bundled

    return os.path.join(get_install_dir(), "ffmpeg")


def resolve_ffmpeg_binary(name: str = "ffmpeg") -> str:
    """Return the path of an ffmpeg binary to run.

    Falls back to the bare binary name so the operating system resolves it
    through ``PATH``, which fails with a clear error only if ffmpeg is missing
    entirely.
    """
    return find_executable(name) or executable_name(name)


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------


#: display adapter names Windows reports, cached for the process lifetime
_display_adapters: list[str] | None = None


def get_display_adapters(refresh: bool = False) -> list[str]:
    """Names of the GPUs Windows has a driver loaded for.

    ``/proc`` and ``/sys`` do not exist on Windows and the driver directories
    that replace them are not readable without a native API, so this asks the
    window manager, which every Windows install has. Every adapter with the
    active flag set is a separate GPU: a laptop with a discrete card reports
    both it and its integrated GPU.
    """
    global _display_adapters

    if _display_adapters is not None and not refresh:
        return _display_adapters

    if not IS_WINDOWS:
        _display_adapters = []
        return _display_adapters

    import ctypes
    from ctypes import wintypes

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    #: DISPLAY_DEVICE_ACTIVE: the adapter is in use, not a mirror driver
    DISPLAY_DEVICE_ACTIVE = 0x1

    adapters: list[str] = []

    try:
        user32 = ctypes.windll.user32
        user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(DISPLAY_DEVICEW),
            wintypes.DWORD,
        ]
        user32.EnumDisplayDevicesW.restype = wintypes.BOOL

        index = 0

        while True:
            device = DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(DISPLAY_DEVICEW)

            if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
                break

            index += 1

            if device.StateFlags & DISPLAY_DEVICE_ACTIVE and device.DeviceString:
                adapters.append(device.DeviceString)
    except (AttributeError, OSError) as err:  # pragma: no cover - needs Windows
        logger.debug("Unable to enumerate display adapters: %s", err)

    _display_adapters = adapters
    return adapters


def classify_adapter_vendors(adapters: list[str]) -> set[str]:
    """Map display adapter names onto the vendors they come from."""
    vendors: set[str] = set()

    for adapter in adapters:
        lowered = adapter.lower()

        if "nvidia" in lowered:
            vendors.add("nvidia")
        elif "amd" in lowered or "radeon" in lowered or "advanced micro" in lowered:
            vendors.add("amd")
        elif "intel" in lowered:
            vendors.add("intel")

    return vendors


def get_display_adapter_vendors(refresh: bool = False) -> set[str]:
    """Vendors of the GPUs Windows reports: nvidia, amd and intel."""
    return classify_adapter_vendors(get_display_adapters(refresh))


def get_cpu_brand() -> str:
    """The processor name as the operating system reports it.

    Linux exposes ``/proc/cpuinfo``, which has no Windows equivalent. The
    registry holds the same marketing name, which is what the Intel generation
    parsing needs.
    """
    if not IS_WINDOWS:
        return ""

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(name).strip()
    except (ImportError, OSError):
        return ""


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------


def get_process_priority_class(priority: int) -> int | None:
    """Map Frigate's POSIX-style priority to a Windows process priority class."""
    if not IS_WINDOWS:
        return None

    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a runtime dependency
        return None

    # Frigate uses nice values between 0 and 19 while Windows only has coarse
    # classes, so use idle for background work and below-normal otherwise.
    if priority >= 10:
        return psutil.IDLE_PRIORITY_CLASS
    elif priority > 0:
        return psutil.BELOW_NORMAL_PRIORITY_CLASS

    return psutil.NORMAL_PRIORITY_CLASS


def set_process_priority(priority: int) -> None:
    """Lower the scheduling priority of the current process.

    ``os.nice`` only exists on POSIX. On Windows the equivalent is a process
    priority class, which psutil maps onto the same range of values Frigate
    already uses for its ffmpeg and detector processes.
    """
    if IS_WINDOWS:
        try:
            import psutil

            value = get_process_priority_class(priority)
            if value is not None:
                psutil.Process().nice(value)
        except Exception as err:  # pragma: no cover - depends on host permissions
            logger.debug("Unable to set process priority: %s", err)

        return

    try:
        os.nice(priority)
    except OSError as err:  # pragma: no cover - depends on host permissions
        logger.debug("Unable to set process priority: %s", err)


def install_signal_handlers(stop_event, exit_callback) -> None:
    """Install the signal handlers used to shut Frigate down cleanly.

    ``SIGTERM`` cannot be delivered on Windows, where closing the console
    window raises ``SIGBREAK`` and Ctrl+C raises ``SIGINT``. Registering a
    handler for a signal that can never arrive is pointless at best, so the set
    of installed handlers differs per platform.
    """
    import signal

    try:
        signal.signal(signal.SIGINT, lambda sig, frame: stop_event.set())
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        pass

    if IS_WINDOWS:
        # Service mode (session 0): no console, so SIGBREAK and SIGINT
        # never arrive. Only register handlers for signals that can actually
        # be delivered by the SCM or the parent launcher.
        service_mode = os.environ.get("FRIGATE_SERVICE_MODE", "0") == "1"
        if not service_mode:
            break_signal = getattr(signal, "SIGBREAK", None)
            if break_signal is not None:
                try:
                    signal.signal(break_signal, lambda sig, frame: stop_event.set())
                except (ValueError, OSError):  # pragma: no cover
                    pass
        else:
            # In session 0, rely on the SCM stop event (handled by the
            # launcher / service wrapper) rather than console signals.
            # Keep SIGINT for completeness if the service is started
            # interactively during testing.
            pass

        return

    try:
        signal.signal(signal.SIGTERM, lambda sig, frame: exit_callback())
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        pass


def install_service_stop_watcher(stop_event) -> bool:
    """Stop Frigate when the Windows service that started it asks it to.

    The service cannot ask with a signal. In session 0 there is no console, so
    no console control event can be delivered, and on Windows every other
    signal is handed to ``TerminateProcess``, which would kill the process
    without finalizing the recording segment it is writing. A named event is
    the one mechanism that crosses the process boundary, so the service creates
    one and passes its name in ``FRIGATE_SERVICE_STOP_EVENT``. This waits on
    that event in a daemon thread and sets the same ``stop_event`` that Ctrl+C
    sets, which means the shutdown is the one Frigate already performs.

    Returns whether a watcher was started. Nothing happens on POSIX, or when
    Frigate was not started by the service, so this can be called
    unconditionally at startup.
    """
    if not IS_WINDOWS:
        return False

    name = os.environ.get(SERVICE_STOP_EVENT_VAR)

    if not name:
        return False

    try:
        import win32event
    except ImportError:  # pragma: no cover - the service itself needs pywin32
        logger.warning("Ignoring %s: pywin32 is not installed", SERVICE_STOP_EVENT_VAR)
        return False

    threading.Thread(
        target=_wait_for_service_stop,
        args=(win32event, name, stop_event),
        name="service-stop",
        daemon=True,
    ).start()

    logger.debug("Watching the service stop event %s", name)
    return True


def _wait_for_service_stop(win32event, name: str, stop_event) -> None:
    """Block until the service signals its stop event, then stop Frigate."""
    #: SYNCHRONIZE, spelled out because win32event does not export it
    synchronize = 0x00100000

    try:
        handle = win32event.OpenEvent(synchronize, False, name)
    except Exception as err:  # pragma: no cover - the service creates it first
        logger.warning("Unable to open the service stop event %s: %s", name, err)
        return

    if not handle:  # pragma: no cover - the service creates it first
        logger.warning("The service stop event %s does not exist", name)
        return

    try:
        win32event.WaitForSingleObject(handle, win32event.INFINITE)
    finally:
        win32event.CloseHandle(handle)

    logger.info("The Windows service asked Frigate to stop")
    stop_event.set()


# ---------------------------------------------------------------------------
# inter process communication
# ---------------------------------------------------------------------------


#: the endpoints Frigate uses, in a fixed order so a port can be derived from
#: the name and stay the same across restarts and processes
IPC_ENDPOINTS = (
    "config",
    "embeddings",
    "comms",
    "detector_pub",
    "detector_sub",
    "proxy_pub",
    "proxy_sub",
    "zmq_detector",
)

#: first port of the loopback range the endpoints are numbered from
IPC_BASE_PORT = 5600

#: port the birdseye restream listener uses on platforms without FIFOs
BIRDSEYE_PORT = 5620


def get_birdseye_port() -> int:
    """Port the birdseye restream listener binds to on Windows."""
    return _env_port(BIRDSEYE_PORT_VAR, BIRDSEYE_PORT)


def birdseye_pipe_path() -> str:
    """Path of the FIFO birdseye frames are restreamed through."""
    return os.path.join(get_cache_dir(), "birdseye")


def birdseye_pipe_source() -> str:
    """Input go2rtc reads raw birdseye frames from.

    A FIFO where the platform has them, otherwise a loopback TCP listener
    (see ``frigate.util.birdseye_pipe``). Both are consumed with
    ``-f rawvideo -pix_fmt yuv420p -video_size WxH -r 10 -i <source>``.
    """
    if IS_WINDOWS:
        return f"tcp://127.0.0.1:{get_birdseye_port()}"

    return birdseye_pipe_path()


def ipc_endpoint(name: str) -> str:
    """ZeroMQ endpoint for a named IPC socket.

    On Linux this is a unix socket in the cache directory. libzmq only builds
    the ``ipc://`` transport on POSIX, so Windows gets a loopback TCP endpoint
    instead. The port is derived from the name rather than assigned by the
    operating system because every process has to arrive at the same endpoint
    without talking to the others first.

    ``FRIGATE_IPC_PORT_BASE`` moves the range, which is only needed when another
    program on the machine already holds one of these ports.
    """
    if IS_WINDOWS:
        base = _env_port(IPC_PORT_BASE_VAR, IPC_BASE_PORT)

        if name in IPC_ENDPOINTS:
            offset = IPC_ENDPOINTS.index(name)
        else:
            # stable across runs, unlike hash(). Unknown names are kept out
            # of the range the known endpoints (and the birdseye listener,
            # 20 ports above the default base) use, so a new endpoint can
            # never silently land on a port another socket already owns
            offset = 100 + zlib.crc32(name.encode()) % 100

        return f"tcp://127.0.0.1:{base + offset}"

    return f"ipc://{os.path.join(get_cache_dir(), name)}"


def _env_port(name: str, default: int) -> int:
    """Read a port number from the environment, falling back on a bad value."""
    value = os.environ.get(name, "").strip()

    if value.isdigit() and 1 <= int(value) <= 65535:
        return int(value)

    return default


# ---------------------------------------------------------------------------
# paths exposed outside of the backend
# ---------------------------------------------------------------------------


def to_media_url_path(path: str | None) -> str | None:
    """Convert a path under the media or cache dir into a URL path.

    ``D:\\Frigate\\media\\clips\\cam\\1.jpg`` becomes ``/clips/cam/1.jpg`` and
    ``/media/frigate/clips/cam/1.jpg`` keeps working, so a browser never has to
    know where recordings live on the machine running Frigate.
    """
    if not path:
        return None

    normalized = str(path).replace("\\", "/")

    # a path under a runtime directory keeps its whole relative path, so a
    # camera named like a media component (a camera called "clips", say)
    # cannot truncate the URL the way a search for the last matching
    # component would
    for base, prefix in (
        (get_media_dir(), ""),
        (get_cache_dir(), f"/{CACHE_URL_COMPONENT}"),
    ):
        base_normalized = str(base).replace("\\", "/").rstrip("/")

        if normalized.startswith(f"{base_normalized}/"):
            return f"{prefix}{normalized[len(base_normalized) :]}"

    for component in MEDIA_URL_COMPONENTS:
        index = normalized.rfind(f"/{component}/")

        if index != -1:
            return normalized[index:]

    return normalized


def unsafe_path_component(path: str) -> bool:
    """Report whether a path contains components that could escape a base dir."""
    return ".." in path.replace("\\", "/").split("/")


def resolve_media_url_path(url_path: str) -> str | None:
    """Convert a URL path served by the web server into a real file path.

    Returns ``None`` for anything that does not name a file under the media or
    cache directory, so a request can never reach outside of them.
    """
    if not url_path:
        return None

    normalized = url_path.replace("\\", "/").lstrip("/")

    if not normalized or unsafe_path_component(normalized):
        return None

    parts = normalized.split("/")
    component = parts[0]
    remainder = parts[1:]

    if component in MEDIA_URL_COMPONENTS:
        # media URLs keep the component: <media>/clips/...
        base = get_media_dir()
        parts = (component, *remainder)
    elif component == CACHE_URL_COMPONENT:
        # cache URLs do not: the cache directory is itself the URL root,
        # the same mapping the native server's /cache mount uses
        base = get_cache_dir()
        parts = tuple(remainder)
    else:
        return None

    candidate = os.path.abspath(os.path.join(base, *parts))
    root = os.path.abspath(base)

    if not is_subpath(candidate, root):
        return None

    return candidate


def is_subpath(path: str, base: str) -> bool:
    """Report whether ``path`` is inside ``base`` without resolving symlinks."""
    root = os.path.abspath(base)
    resolved = os.path.abspath(path)

    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        # different drives on Windows, so the paths are not comparable
        return False


def get_fs_type(path: str) -> str:
    """Filesystem type of the volume holding a path."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        return "unknown"

    best_match = ""
    fs_type = ""

    try:
        partitions = psutil.disk_partitions(all=True)
    except Exception:  # pragma: no cover - platform dependent
        return "unknown"

    for part in partitions:
        mountpoint = part.mountpoint

        if not mountpoint:
            continue

        # a plain prefix match would report the filesystem of "/media" for
        # a path under "/media2", so the mountpoint has to contain the path
        # on a component boundary
        if is_subpath(path, mountpoint) and len(best_match) < len(mountpoint):
            fs_type = part.fstype
            best_match = mountpoint

    return fs_type


def describe() -> str:
    """One line summary of the platform and paths, used in the startup log."""
    return (
        f"{sys.platform} | python {sys.version.split()[0]} | "
        f"install={get_install_dir()} config={get_config_dir()} "
        f"media={get_media_dir()} cache={get_cache_dir()} tmp={get_tmp_dir()}"
    )


# ---------------------------------------------------------------------------
# long paths
# ---------------------------------------------------------------------------

#: Legacy Win32 limit, still enforced for paths that are not long path aware.
MAX_PATH_LIMIT = 260

_LONG_PATHS_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"


def long_paths_enabled() -> bool:
    """Report whether this machine allows paths longer than ``MAX_PATH``.

    Windows 10 1607 and later lift the limit when the machine wide
    ``LongPathsEnabled`` value is set, so this is what decides whether a long
    recording path is a real failure or just a cosmetically long string.
    """
    if not IS_WINDOWS:
        return True

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _LONG_PATHS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False

    return bool(value)


def longest_recording_path(camera_names: Iterable[str]) -> str:
    """The longest recording path Frigate can build for these cameras.

    Recordings are written to
    ``recordings/<date>/<hour>/<camera>/<minute>.<second>.mp4``, so everything
    after the media directory is a fixed width except the camera name. That
    makes this path the deepest one a native install creates and the one worth
    measuring against ``MAX_PATH``.
    """
    camera = max(camera_names, key=len, default="camera")
    now = datetime.datetime.now()

    return os.path.join(
        get_media_dir(),
        "recordings",
        now.strftime("%Y-%m-%d"),
        now.strftime("%H"),
        camera,
        now.strftime("%M.%S.mp4"),
    )


def long_path_warning(camera_names: Iterable[str]) -> str | None:
    """Warn when recording paths can overrun the Win32 path limit.

    Returns ``None`` off Windows, when long paths are enabled, or when the
    longest path Frigate builds still fits. Nothing is truncated or rewritten:
    a path that is too long fails inside ``ffmpeg`` while the camera is already
    running, so the only useful moment to say so is at startup.
    """
    if not IS_WINDOWS:
        return None

    longest = longest_recording_path(camera_names)

    if len(longest) < MAX_PATH_LIMIT or long_paths_enabled():
        return None

    return (
        f"Recording paths can reach {len(longest)} characters, which is past the "
        f"{MAX_PATH_LIMIT} character Win32 limit, so writing a segment can fail "
        f"with a path error once a camera is running. Shorten FRIGATE_BASE_DIR "
        f"(currently {get_base_dir()}) or the camera names, or allow long paths "
        f"by setting the LongPathsEnabled registry value to 1."
    )
