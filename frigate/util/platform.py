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

import logging
import os
import shutil
import sys
import zlib
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

    if override.isdigit():
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
# processes
# ---------------------------------------------------------------------------


def set_process_priority(priority: int) -> None:
    """Lower the scheduling priority of the current process.

    ``os.nice`` only exists on POSIX. On Windows the equivalent is a process
    priority class, which psutil maps onto the same range of values Frigate
    already uses for its ffmpeg and detector processes.
    """
    if IS_WINDOWS:
        try:
            import psutil

            # Frigate uses nice values between 0 and 19 while Windows only has
            # coarse classes, so map onto the two that make sense here: idle for
            # background work, below normal for everything else.
            if priority >= 10:
                value = psutil.IDLE_PRIORITY_CLASS
            elif priority > 0:
                value = psutil.BELOW_NORMAL_PRIORITY_CLASS
            else:
                value = psutil.NORMAL_PRIORITY_CLASS

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
        break_signal = getattr(signal, "SIGBREAK", None)

        if break_signal is not None:
            try:
                signal.signal(break_signal, lambda sig, frame: stop_event.set())
            except (ValueError, OSError):  # pragma: no cover
                pass

        return

    try:
        signal.signal(signal.SIGTERM, lambda sig, frame: exit_callback())
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        pass


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
            # stable across runs, unlike hash()
            offset = zlib.crc32(name.encode()) % 100

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
        base = get_media_dir()
    elif component == CACHE_URL_COMPONENT:
        base = get_cache_dir()
    else:
        return None

    candidate = os.path.abspath(os.path.join(base, component, *remainder))
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

        if path.startswith(mountpoint) and len(best_match) < len(mountpoint):
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
