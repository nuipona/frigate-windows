"""Make sure child processes die when Frigate does, on Windows.

Frigate runs a lot of processes: one ffmpeg per camera stream, go2rtc for live
view, and a multiprocessing child for detection, recording, output and each
other service. Several of those children start ffmpeg processes of their own.

``multiprocessing.Process.terminate()`` calls ``TerminateProcess`` and can
leave grandchildren such as FFmpeg running after their parent is gone.

Orphans hold on to the loopback ports Frigate numbers its IPC endpoints from
(see :func:`frigate.util.platform.ipc_endpoint`), so the next start cannot bind
them; they keep pulling streams from the cameras; and they keep recording
segments open, which turns the recording maintainer's move into a
``PermissionError``.

A Windows Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` fixes the
whole class of problem. Membership is inherited by every child, and when the
last handle to the job is closed every remaining member is terminated. Frigate
never closes the handle itself, so the operating system does it as soon as the
process exits -- including when it crashes or is killed, which is exactly the
case nothing else covers.

Two things are worth knowing about holding the handle open:

- Closing it while children are still running would kill them immediately, so
  the handle lives for the lifetime of the process.
- A Frigate that is already inside a job (started by a service wrapper, for
  example) can only be added to a second one on Windows 8 and later, which
  added nested jobs. Failure is logged and ignored, leaving the previous
  behaviour in place.

Set ``FRIGATE_NO_JOB_OBJECT=1`` to opt out entirely.
"""

import ctypes
import logging
import os
from ctypes import wintypes

#: Win32 DWORD is always 32 bits.
_DWORD = ctypes.c_uint32

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

#: environment variable that turns the job object off
DISABLE_VAR = "FRIGATE_NO_JOB_OBJECT"

_TRUTHY = ("1", "true", "yes", "on")

# JOBOBJECT_INFORMATION_CLASS
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

# JOB_OBJECT_LIMIT_FLAGS
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

# The handle has to stay open for as long as the process lives: closing it
# kills every member of the job, and leaving it open is what makes the
# operating system close it for us when this process exits.
_handle = None

#: why the job object is not in use, for the startup log and for tests
_error: str | None = None


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _last_error_text() -> str:
    """Text for the last Windows error, which only exists on Windows."""
    get_last_error = getattr(ctypes, "get_last_error", None)
    format_error = getattr(ctypes, "FormatError", None)

    if get_last_error is None or format_error is None:
        return "not running on Windows"

    code = get_last_error()

    try:
        return f"{format_error(code).strip()} ({code})"
    except Exception:  # pragma: no cover - depends on the error code
        return str(code)


def install() -> bool:
    """Put this process in a job that kills its children when it exits.

    Returns whether the job object is in place. Calling it more than once is
    harmless; only the first call does anything.
    """
    global _handle, _error

    if not IS_WINDOWS:
        return False

    if _handle is not None:
        return True

    if _error is not None:
        return False

    if os.environ.get(DISABLE_VAR, "").strip().lower() in _TRUTHY:
        _error = f"disabled by {DISABLE_VAR}"
        logger.debug("Windows job object disabled by %s", DISABLE_VAR)
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    create_job_object = kernel32.CreateJobObjectW
    create_job_object.restype = wintypes.HANDLE
    create_job_object.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

    set_information_job_object = kernel32.SetInformationJobObject
    set_information_job_object.restype = wintypes.BOOL
    set_information_job_object.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        _DWORD,
    ]

    assign_process_to_job_object = kernel32.AssignProcessToJobObject
    assign_process_to_job_object.restype = wintypes.BOOL
    assign_process_to_job_object.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = wintypes.HANDLE
    get_current_process.argtypes = []

    close_handle = kernel32.CloseHandle
    close_handle.restype = wintypes.BOOL
    close_handle.argtypes = [wintypes.HANDLE]

    job = create_job_object(None, None)

    if not job:
        _error = f"CreateJobObjectW failed: {_last_error_text()}"
        logger.warning("Unable to create a Windows job object: %s", _error)
        return False

    info = _JobObjectExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    if not set_information_job_object(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        _error = f"SetInformationJobObject failed: {_last_error_text()}"
        logger.warning("Unable to configure the Windows job object: %s", _error)
        close_handle(job)
        return False

    if not assign_process_to_job_object(job, get_current_process()):
        _error = f"AssignProcessToJobObject failed: {_last_error_text()}"
        logger.warning(
            "Unable to join the Windows job object (%s). Child processes will "
            "not be cleaned up automatically if Frigate exits unexpectedly; "
            "set %s=1 to silence this warning.",
            _error,
            DISABLE_VAR,
        )
        close_handle(job)
        return False

    # deliberately not closed: the handle is what keeps the job alive, and
    # the operating system closes it when this process exits, which is the
    # moment every remaining member of the job is terminated
    _handle = job
    logger.debug("Windows job object installed; children die with this process")
    return True


def installed() -> bool:
    """Report whether :func:`install` succeeded."""
    return _handle is not None


def error() -> str | None:
    """Why the job object is not in use, or None when it is."""
    return _error


def reset() -> None:
    """Forget any installed job. Only for tests."""
    global _handle, _error
    _handle = None
    _error = None


__all__ = [
    "DISABLE_VAR",
    "IS_WINDOWS",
    "error",
    "install",
    "installed",
    "reset",
]
