"""Native Windows paths, processes, hardware APIs, and IPC helpers.

The application has one runtime platform. Files live below a writable Windows
application-data root, child processes use ``spawn``, local IPC uses loopback
TCP, and external tools are Windows executables. ``IS_WINDOWS`` is retained
only so imports and unit tests can fail cleanly on a development host; startup
calls :func:`require_windows` before creating any resources.
"""

import datetime
import logging
import os
import shutil
import subprocess
import sys
import threading
import zlib
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"


class UnsupportedPlatformError(RuntimeError):
    """Raised when the Windows application is started on another OS."""


def require_windows() -> None:
    """Stop before startup reaches Windows-specific services or drivers."""
    if not IS_WINDOWS:
        raise UnsupportedPlatformError(
            "This application runs only on native Windows. "
            f"The current platform is {sys.platform!r}."
        )


BASE_DIR_VAR = "FRIGATE_BASE_DIR"
CONFIG_DIR_VAR = "FRIGATE_CONFIG_DIR"
MEDIA_DIR_VAR = "FRIGATE_MEDIA_DIR"
CACHE_DIR_VAR = "FRIGATE_CACHE_DIR"
TMP_DIR_VAR = "FRIGATE_TMP_DIR"
SERVICE_STOP_EVENT_VAR = "FRIGATE_SERVICE_STOP_EVENT"
WEB_DIR_VAR = "FRIGATE_WEB_DIR"
BIRDSEYE_PORT_VAR = "FRIGATE_BIRDSEYE_PORT"
FFMPEG_DIR_VAR = "FRIGATE_FFMPEG_DIR"
IPC_PORT_BASE_VAR = "FRIGATE_IPC_PORT_BASE"
WEB_HOST_VAR = "FRIGATE_WEB_HOST"
WEB_PORT_VAR = "FRIGATE_WEB_PORT"

INTERNAL_API_PORT = 5001
MEDIA_URL_COMPONENTS = ("clips", "recordings", "exports")
CACHE_URL_COMPONENT = "cache"


def _env_path(name: str) -> str | None:
    value = os.environ.get(name)
    if not value or not value.strip():
        return None
    return os.path.abspath(os.path.expanduser(os.path.expandvars(value.strip())))


def base_dir_is_overridden() -> bool:
    return _env_path(BASE_DIR_VAR) is not None


def get_source_root() -> str:
    """Directory containing the package, migrations, and web build."""
    return str(Path(__file__).resolve().parents[2])


def get_default_labelmap_path() -> str:
    return os.path.join(get_source_root(), "labelmap.txt")


def get_audio_labelmap_path() -> str:
    return os.path.join(get_source_root(), "audio-labelmap.txt")


def get_audio_model_path() -> str:
    return os.path.join(get_model_cache_dir(), "cpu_audio_model.tflite")


def get_install_dir() -> str:
    return get_source_root()


def get_base_dir() -> str:
    """Writable root, defaulting to ``%LOCALAPPDATA%\\Frigate``."""
    override = _env_path(BASE_DIR_VAR)
    if override:
        return override

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return os.path.abspath(os.path.join(local_app_data, "Frigate"))
    return os.path.abspath(os.path.join(os.path.expanduser("~"), "Frigate"))


def _runtime_dir(variable: str, child: str) -> str:
    return _env_path(variable) or os.path.join(get_base_dir(), child)


def get_config_dir() -> str:
    return _runtime_dir(CONFIG_DIR_VAR, "config")


def get_media_dir() -> str:
    return _runtime_dir(MEDIA_DIR_VAR, "media")


def get_cache_dir() -> str:
    return _runtime_dir(CACHE_DIR_VAR, "cache")


def get_tmp_dir() -> str:
    return _runtime_dir(TMP_DIR_VAR, "tmp")


def get_log_dir() -> str:
    return os.path.join(get_tmp_dir(), "logs")


def get_model_cache_dir() -> str:
    return os.path.join(get_config_dir(), "model_cache")


VITE_DEV_ENTRY_MARKER = "/src/main.tsx"


def _is_built_web_dir(candidate: str) -> bool:
    index = os.path.join(candidate, "index.html")
    if not os.path.isfile(index):
        return False
    try:
        with open(index, encoding="utf-8", errors="replace") as file:
            return VITE_DEV_ENTRY_MARKER not in file.read()
    except OSError:
        return False


def get_web_dir() -> str:
    """Directory containing the prebuilt native web interface."""
    override = _env_path(WEB_DIR_VAR)
    if override:
        return override

    candidates = (
        os.path.join(get_install_dir(), "web", "dist"),
        os.path.join(get_install_dir(), "web"),
    )
    for candidate in candidates:
        if _is_built_web_dir(candidate):
            return candidate
    return candidates[0]


def web_dir_has_build() -> bool:
    return _is_built_web_dir(get_web_dir())


def ensure_runtime_dirs() -> None:
    """Create the Windows data layout before services start."""
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


def serves_web_app() -> bool:
    """The embedded server always serves the API, media, and web app."""
    return True


def get_web_host() -> str:
    return os.environ.get(WEB_HOST_VAR, "").strip() or "0.0.0.0"


def get_web_port(default_port: int) -> int:
    override = os.environ.get(WEB_PORT_VAR, "").strip()
    if override.isdigit() and 1 <= int(override) <= 65535:
        return int(override)
    return default_port


STORAGE_KEYS = {
    "recordings": "recordings",
    "clips": "clips",
    "cache": "cache",
    "shm": "memory",
}


def storage_stat_key(name: str, path: str | None = None) -> str:
    return STORAGE_KEYS.get(name, path or name)


def ffmpeg_stdin_input() -> str:
    return "pipe:0"


def ffmpeg_concat_file_line(path: str) -> str:
    normalized = str(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{normalized}'"


def _split_windows_command(command: str) -> list[str]:
    """Split a command using the Microsoft C runtime quoting rules."""
    args: list[str] = []
    current: list[str] = []
    has_argument = False
    quoted = False
    index = 0

    while index < len(command):
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
            while index < len(command) and command[index] == "\\":
                index += 1
            backslashes = index - start
            if index < len(command) and command[index] == '"':
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
    return subprocess.list2cmdline([str(value)])


def split_ffmpeg_command(command: str) -> list[str]:
    return _split_windows_command(command)


def get_service_log_path(service: str) -> str:
    return os.path.join(get_log_dir(), service, "current")


def get_mp_start_method() -> str:
    return "spawn"


def get_shared_memory_total_mb() -> float | None:
    """Windows shared memory is page-file backed and has no /dev/shm quota."""
    return None


EXECUTABLE_SUFFIX = ".exe"


def executable_name(name: str) -> str:
    return name if name.lower().endswith(".exe") else f"{name}.exe"


def get_ffmpeg_dir() -> str:
    override = _env_path(FFMPEG_DIR_VAR)
    if override:
        return override
    bundled = os.path.join(get_config_dir(), "ffmpeg")
    if os.path.isdir(bundled):
        return bundled
    return os.path.join(get_install_dir(), "ffmpeg")


def _path_candidates() -> list[str]:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", "")
    return [
        get_ffmpeg_dir(),
        os.path.join(get_ffmpeg_dir(), "bin"),
        os.path.join(get_install_dir(), "bin"),
        os.path.join(local_app_data, "Microsoft", "WinGet", "Links"),
        r"C:\ProgramData\chocolatey\bin",
        os.path.join(user_profile, "scoop", "shims"),
        r"C:\ffmpeg\bin",
    ]


def find_executable(name: str, extra_dirs: tuple[str, ...] | None = None) -> str | None:
    binary = executable_name(name)
    found = shutil.which(binary) or shutil.which(name)
    if found:
        return found

    for directory in tuple(extra_dirs or ()) + tuple(_path_candidates()):
        if directory:
            candidate = os.path.join(directory, binary)
            if os.path.isfile(candidate):
                return candidate
    return None


def resolve_ffmpeg_binary(name: str = "ffmpeg") -> str:
    return find_executable(name) or executable_name(name)


_display_adapters: list[str] | None = None


def get_display_adapters(refresh: bool = False) -> list[str]:
    """Enumerate active display adapters through Win32 ``EnumDisplayDevices``."""
    global _display_adapters
    if _display_adapters is not None and not refresh:
        return _display_adapters
    if not IS_WINDOWS:  # enables import-only tooling on non-Windows hosts
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

    active = 0x1
    adapters: list[str] = []
    try:
        enum = ctypes.windll.user32.EnumDisplayDevicesW
        enum.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(DISPLAY_DEVICEW),
            wintypes.DWORD,
        ]
        enum.restype = wintypes.BOOL
        index = 0
        while True:
            device = DISPLAY_DEVICEW()
            device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            if not enum(None, index, ctypes.byref(device), 0):
                break
            index += 1
            if device.StateFlags & active and device.DeviceString:
                adapters.append(device.DeviceString)
    except (AttributeError, OSError) as err:  # pragma: no cover - Windows API
        logger.debug("Unable to enumerate display adapters: %s", err)

    _display_adapters = adapters
    return adapters


def classify_adapter_vendors(adapters: list[str]) -> set[str]:
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
    return classify_adapter_vendors(get_display_adapters(refresh))


def get_cpu_brand() -> str:
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


def get_process_priority_class(priority: int) -> int | None:
    try:
        import psutil

        if priority >= 10:
            return psutil.IDLE_PRIORITY_CLASS
        if priority > 0:
            return psutil.BELOW_NORMAL_PRIORITY_CLASS
        return psutil.NORMAL_PRIORITY_CLASS
    except (ImportError, AttributeError):
        return None


def set_process_priority(priority: int) -> None:
    try:
        import psutil

        value = get_process_priority_class(priority)
        if value is not None:
            psutil.Process().nice(value)
    except Exception as err:  # pragma: no cover - host permissions
        logger.debug("Unable to set process priority: %s", err)


def install_signal_handlers(stop_event, exit_callback) -> None:
    """Install handlers for Windows console control events."""
    import signal

    try:
        signal.signal(signal.SIGINT, lambda sig, frame: stop_event.set())
    except (ValueError, OSError):
        pass

    if os.environ.get("FRIGATE_SERVICE_MODE", "0") != "1":
        break_signal = getattr(signal, "SIGBREAK", None)
        if break_signal is not None:
            try:
                signal.signal(break_signal, lambda sig, frame: stop_event.set())
            except (ValueError, OSError):
                pass


def install_service_stop_watcher(stop_event) -> bool:
    """Watch the named event through which the SCM requests graceful stop."""
    if not IS_WINDOWS:
        return False
    name = os.environ.get(SERVICE_STOP_EVENT_VAR)
    if not name:
        return False
    try:
        import win32event
    except ImportError:  # pragma: no cover - pywin32 is required on Windows
        logger.warning("Ignoring %s: pywin32 is not installed", SERVICE_STOP_EVENT_VAR)
        return False

    threading.Thread(
        target=_wait_for_service_stop,
        args=(win32event, name, stop_event),
        name="service-stop",
        daemon=True,
    ).start()
    return True


def _wait_for_service_stop(win32event, name: str, stop_event) -> None:
    synchronize = 0x00100000
    try:
        handle = win32event.OpenEvent(synchronize, False, name)
    except Exception as err:  # pragma: no cover - service creates it first
        logger.warning("Unable to open service stop event %s: %s", name, err)
        return
    if not handle:
        logger.warning("The service stop event %s does not exist", name)
        return
    try:
        win32event.WaitForSingleObject(handle, win32event.INFINITE)
    finally:
        handle.Close()
    logger.info("The Windows service asked Frigate to stop")
    stop_event.set()


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
IPC_BASE_PORT = 5600
BIRDSEYE_PORT = 5620


def _env_port(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if value.isdigit() and 1 <= int(value) <= 65535:
        return int(value)
    return default


def get_birdseye_port() -> int:
    return _env_port(BIRDSEYE_PORT_VAR, BIRDSEYE_PORT)


def birdseye_pipe_path() -> str:
    """Legacy name for the birdseye endpoint's cache-side metadata path."""
    return os.path.join(get_cache_dir(), "birdseye")


def birdseye_pipe_source() -> str:
    return f"tcp://127.0.0.1:{get_birdseye_port()}"


def ipc_endpoint(name: str) -> str:
    """Stable loopback TCP endpoint for local ZeroMQ communication."""
    base = _env_port(IPC_PORT_BASE_VAR, IPC_BASE_PORT)
    if name in IPC_ENDPOINTS:
        offset = IPC_ENDPOINTS.index(name)
    else:
        offset = 100 + zlib.crc32(name.encode()) % 100
    return f"tcp://127.0.0.1:{base + offset}"


def to_media_url_path(path: str | None) -> str | None:
    if not path:
        return None
    normalized = str(path).replace("\\", "/")

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
    return ".." in path.replace("\\", "/").split("/")


def resolve_media_url_path(url_path: str) -> str | None:
    if not url_path:
        return None
    normalized = url_path.replace("\\", "/").lstrip("/")
    if not normalized or unsafe_path_component(normalized):
        return None

    parts = normalized.split("/")
    component = parts[0]
    remainder = parts[1:]
    if component in MEDIA_URL_COMPONENTS:
        base = get_media_dir()
        parts = [component, *remainder]
    elif component == CACHE_URL_COMPONENT:
        base = get_cache_dir()
        parts = remainder
    else:
        return None

    candidate = os.path.abspath(os.path.join(base, *parts))
    return candidate if is_subpath(candidate, base) else None


def is_subpath(path: str, base: str) -> bool:
    root = os.path.abspath(base)
    resolved = os.path.abspath(path)
    try:
        return os.path.commonpath([resolved, root]) == root
    except ValueError:
        return False


def get_fs_type(path: str) -> str:
    try:
        import psutil

        partitions = psutil.disk_partitions(all=True)
    except Exception:
        return "unknown"

    best_match = ""
    fs_type = ""
    for part in partitions:
        mountpoint = part.mountpoint
        if (
            mountpoint
            and is_subpath(path, mountpoint)
            and len(best_match) < len(mountpoint)
        ):
            fs_type = part.fstype
            best_match = mountpoint
    return fs_type


def describe() -> str:
    return (
        f"Windows | python {sys.version.split()[0]} | "
        f"install={get_install_dir()} config={get_config_dir()} "
        f"media={get_media_dir()} cache={get_cache_dir()} tmp={get_tmp_dir()}"
    )


MAX_PATH_LIMIT = 260
_LONG_PATHS_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"


def long_paths_enabled() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _LONG_PATHS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except OSError:
        return False


def longest_recording_path(camera_names: Iterable[str]) -> str:
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
    if not IS_WINDOWS:
        return None
    longest = longest_recording_path(camera_names)
    if len(longest) < MAX_PATH_LIMIT or long_paths_enabled():
        return None
    return (
        f"Recording paths can reach {len(longest)} characters, past the "
        f"{MAX_PATH_LIMIT}-character Win32 limit. Shorten FRIGATE_BASE_DIR "
        f"(currently {get_base_dir()}) or camera names, or enable the "
        "LongPathsEnabled registry value."
    )
