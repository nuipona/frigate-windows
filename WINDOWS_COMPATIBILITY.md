# Windows Compatibility: Current State and Recommendations

An audit of this fork against the goal of running Frigate natively on Windows,
with no Docker and no WSL. Line references are against the current tree.

---

## Upstream parity

This fork tracks `blakeblackshear/frigate` `dev`. **Last synced to upstream
`e61ad52b` (2026-10-12)**, which brought in two commits this fork was missing. **Updated: upstream `b79e043` (Reduce idle CPU from thread pools, #24652) merged on `arena/3d44d5f3-frigate-windows` (2026-10-13)** — `get_ort_session_options()` now accepts the `cpu` provider flag and disables `session.intra_op.allow_spinning` for CPU enrichment sessions; `frigate/embeddings/__init__.py` and `frigate/output/output.py` set `cv2.setNumThreads(1)`.

| Upstream commit | What it changes here |
| --- | --- |
| `ff9eb8f1` Keep frame shared memory mapped (#24644) | `FrameManager` grows `frame_times()` / `close_camera()` and `get()` takes a `frame_time`, so a reader can tell a lapped ring slot from a frame still being read. Merged on top of this fork's Windows fix in `capture_frames`, which slices `frame_buffer[:frame_size]` because a Windows segment is mapped page aligned. |
| `e61ad52b` move env helpers out of the config package (#24648) | `frigate/config/env.py` → `frigate/util/env.py`. This fork had already moved the go2rtc config generator into `frigate/util/go2rtc_config.py`, so the import moved there rather than in `create_config.py`. |
| `b79e043` Reduce idle CPU from thread pools (#24652) | `get_ort_session_options()` gains `cpu: bool`; CPU enrichment disables `intra_op.allow_spinning`; embeddings/output processes set `cv2.setNumThreads(1)`. Merged directly — no Windows branch conflicts. |

The Windows port deliberately *adapts* rather than removes upstream behaviour.
Everything it swaps out was checked to produce byte-identical results on Linux:
the `frigate/const.py` directory constants, all eight ZeroMQ `ipc://` endpoints
(now built by `platform.ipc_endpoint()`), the `service.storage` stat keys the
web UI reads (now built by `platform.storage_stat_key()`),
`resolve_ffmpeg_path()` against the image's `DEFAULT_FFMPEG_VERSION` /
`INCLUDED_FFMPEG_VERSIONS`, the `/labelmap.txt` default, and the
`openvino:CPU` first-run detector default.

The remaining gaps are hardware probes with no Windows equivalent
(`frigate/util/services.py`: libva, DRM sysfs, Rockchip, Jetson, Hailo, Axiom),
which return empty on Windows and are tracked under
[P3-3](#p3-3-detector-coverage-on-windows-is-narrow).

To re-check parity after an upstream merge, diff this tree against upstream
`dev` and confirm every remaining difference is an `IS_WINDOWS` branch or a
file this fork adds:

```
git fetch https://github.com/blakeblackshear/frigate.git dev
git diff --stat FETCH_HEAD -- .
```

---

## TL;DR

The hard architectural work was **already done and done well**. This is not a
codebase that needs porting; it needed a correctness pass on process lifecycle,
a proper packaging story, and a much wider Windows test net.

**Update:** the four P0 process-lifecycle bugs below are now fixed, with unit
tests for each branch and a new end-to-end CI job
(`windows_native_e2e`) that boots Frigate against a synthetic camera on
`windows-latest`, records, stops it through the same restart path the UI uses,
and asserts that it shut down cleanly, left no orphaned processes and left no
truncated recordings. Neither the fixes nor the end-to-end test has been run on
real Windows yet — see [Part 5](#part-5--what-is-still-unverified).

What remains, in rough order of cost to the user:

1. ~~**There is no installer, no service, and no bundled ffmpeg/go2rtc**~~ —
   the bootstrap script (`py -3.12 bootstrap_windows.py`) now covers the whole
   install and writes a restartable launcher, and releases ship a bundle with
   the web interface prebuilt. An SCM service and a winget manifest are the
   remaining pieces ([P3-1](#p3-1-no-windows-packaging-or-service-story)).
2. ~~**Windows CI is still a thin allowlist**~~ — the whole suite runs on
   Windows through the denylist job
   ([P3-2](#p3-2-windows-ci-is-a-thin-allowlist)).
3. ~~**Detector coverage is CPU and Intel only**~~ — DirectML now covers
   AMD, Intel and Nvidia GPU inference from one optional package
   ([P3-3](#p3-3-detector-coverage-on-windows-is-narrow)).

**Update 3:** the remaining P2 and P3 items are closed or narrowed:

| Finding | Status |
| --- | --- |
| [P2-5](#p2-5-filelock-on-windows-needs-a-real-cross-process-test) cross-process `FileLock` | **fixed** — `frigate/test/test_file.py::TestFileLockAcrossProcesses` locks against a genuinely separate `python` process: a held lock is denied to a second process, a released lock is takeable, and a holder killed without releasing does not keep the lock. The same three tests cover `flock` and `msvcrt.locking` |
| [P2-7](#p2-7-timezone-and-dst-across-a-long-lived-process) DST | **fixed (documented)** — ordinary DST transitions resolve themselves on Windows; the docs now say a *time zone change* while Frigate runs needs a restart, which is the case that actually goes stale |
| [P2-8](#p2-8-smaller-items) virtual NICs | **fixed** — `get_physical_interfaces()` drops down, loopback, Bluetooth, VPN/tunnel and Hyper-V style adapters and interfaces that are not up, and keeps only configured prefixes |
| [P2-8](#p2-8-smaller-items) `/dev/shm` label | **fixed** — the storage panel shows "Page file (shared memory)" when the stats say `page_file_backed`, and suppresses the "increase SHM" warning there (there is no size to raise); the backend notice no longer fires for page-file-backed shared memory either |
| [P3-1](#p3-1-no-windows-packaging-or-service-story)(1)(2) | **done** — `bootstrap_windows.py` (stdlib only) creates the venv, installs the requirements, fetches pinned FFmpeg 8.1.2 and go2rtc 1.9.14 with first-use recorded digests, builds the web UI when Node is present, and writes `run_frigate.ps1` plus a Start Menu shortcut. `release.yml` builds a Windows bundle with the web interface prebuilt and attaches it to releases, so Node is never required |
| [P3-1](#p3-1-no-windows-packaging-or-service-story)(3) service | **implemented** — `frigate/util/windows_service.py` (pywin32 ServiceFramework), `scripts/install_frigate_service.ps1` (install/start/stop/remove), `bootstrap_windows.py --install-service`, and a service-aware `install_signal_handlers()` branch that skips console-only signals in session 0. Needs a real Windows service-mode end-to-end run. |
| [P3-3](#p3-3-detector-coverage-on-windows-is-narrow) | **partly** — DirectML: `onnxruntime-directml` is documented as the cross-vendor GPU detector (`bootstrap_windows.py --directml` performs the swap), the provider flows through `get_ort_providers()` untouched, and the runner reports it as "DirectML". Config validation: a device string naming a Linux-only detector now fails at startup with a message naming what Windows can run, and a runtime whose import failed says why instead of "does not name a known detector" |

SCM service framework implemented (`windows_service.py`, install script,
signal branch, bootstrap option) — needs real service-mode end-to-end
validation. A basic winget manifest (`winget-manifest/Frigate.FrigateWindows.yaml`)
has been added but needs validation and a signed installer URL before
submission.

**Update 2:** the P1 and P2 findings below have since been fixed, and a
`windows_native_unit_suite` CI job now runs the whole `frigate/test` discovery
on `windows-latest` through `testing-scripts/run_windows_unit_suite.py`, with
`frigate/test/windows_suite_denylist.txt` naming the tests that genuinely cannot
run there. The job is `continue-on-error` until that denylist is complete, so a
Linux-only test is visible instead of invisible. Closed in that change:

| Finding | Status |
| --- | --- |
| [P1-1](#p1-1-hwaccel_args-auto-can-never-detect-windows-hardware) `auto` on Windows | **fixed** — go2rtc sources first, display adapters as the fallback, returns `preset-windows-nvenc` / `-qsv` / `-d3d11va` |
| [P1-2](#p1-2-the-docs-recommend-a-preset-that-does-not-exist-for-decode) docs recommend a decode preset that did not exist | **fixed** — real Windows decode presets, and an unknown or Linux-only preset is now a config validation error naming the valid ones |
| [P1-3](#p1-3-the-hwaccel-recommender-can-emit-invalid-linux-presets-on-windows) recommender emits Linux presets | **fixed** — Windows families, `_intel_generation()` reads the CPU brand from `winreg` |
| [P2-1](#p2-1-windows-reserved-device-names-are-valid-camera-names) reserved device names | **fixed** — rejected on Windows only |
| [P2-2](#p2-2-path-length) path length | **mitigated** — startup warning with the base directory and the registry value; paths are not rewritten |
| [P2-3](#p2-3-case-insensitive-filesystem) case-insensitive filesystem | **fixed** — camera names that differ only by case are rejected on Windows |
| [P2-4](#p2-4-antivirus-and-indexing-hold-files-open) antivirus holds files open | **fixed** — `robust_remove` / `robust_move` with backoff, used by the record maintainer, the output process and preview frames, plus Defender exclusion docs |
| [P2-6](#p2-6-shlexsplitposixtrue-mangles-unquoted-windows-paths) `shlex` mangles Windows paths | **fixed** — a Windows command splitter, POSIX behaviour unchanged |
| [P2-8](#p2-8-smaller-items) smaller items | **partly** — `find_go2rtc()` suffix check, `hwaccel.py` Windows guard, requirements drift check. Virtual-NIC filtering and the `/dev/shm` label are open |
| [P3-2](#p3-2-windows-ci-is-a-thin-allowlist) thin Windows CI | **partly** — the whole suite runs on Windows now, with the allowlist inverted into a denylist |

SCM service framework implemented — needs real service-mode end-to-end
validation. A basic winget manifest has been added but needs validation
and a signed installer URL.
([P3-1](#p3-1-no-windows-packaging-or-service-story)); everything else from
the P2 and P3 list is fixed, narrowed, or tracked in the table above.

---

## Part 1 — What we have

### The abstraction layer

`frigate/util/platform.py` (857 lines) is the keystone, and it is genuinely
well designed. Its stated rules — import nothing from Frigate outside the
module (because `frigate.const` imports it and nearly everything imports
`frigate.const`), never import a POSIX-only module at module scope, and return
every path as an absolute OS-native path — are the right rules, and they are
followed.

| Concern | How it is handled |
| --- | --- |
| Platform detection | `IS_WINDOWS` / `IS_POSIX` / `IS_LINUX` / `IS_MACOS` (`platform.py:48`) |
| Directory layout | One base dir (`%LOCALAPPDATA%\Frigate` by default), overridable per-directory via `FRIGATE_BASE_DIR`, `FRIGATE_CONFIG_DIR`, `FRIGATE_MEDIA_DIR`, `FRIGATE_CACHE_DIR`, `FRIGATE_TMP_DIR` |
| Binary discovery | `find_executable()` checks `PATH`, then install dir, bundled ffmpeg dir, and winget/chocolatey/scoop shim dirs; `EXECUTABLE_SUFFIX` appends `.exe` |
| Process priority | `os.nice()` → `psutil` priority classes (`IDLE` / `BELOW_NORMAL` / `NORMAL`) |
| Signals | `install_signal_handlers()` installs `SIGINT` + `SIGBREAK` on Windows, `SIGINT` + `SIGTERM` on POSIX |
| IPC | Unix sockets → loopback TCP on a deterministic port range derived from the endpoint name (`5600+`, `platform.py:681`) |
| Birdseye restream | FIFO → loopback TCP listener |
| Shared memory | `/dev/shm` accounting skipped; page-file-backed; `get_shared_memory_total_mb()` returns `None` |
| Media URLs | `to_media_url_path()` / `resolve_media_url_path()` map `D:\Frigate\media\clips\...` ↔ `/clips/...` so the browser never learns the real layout |
| Path safety | `is_subpath()` catches the `ValueError` `commonpath` raises across different Windows drives |
| ffmpeg | `ffmpeg_stdin_input()` → `pipe:0`; `ffmpeg_concat_file_line()` normalizes backslashes (the concat demuxer treats `\` as an escape) |

### The nginx replacement

`frigate/api/web.py` + `frigate/api/vod_native.py` rebuild the container's nginx
facade in front of the same FastAPI app: static web app, media mounts,
`/vod/*` HLS built in Python, websocket and go2rtc proxying, and an in-process
ASGI `auth_request` call instead of an HTTP subrequest. That last detail is a
real trap (a loopback auth request re-enters the server that is handling it and
deadlocks a single-threaded server), and the code calls it out and avoids it.
This is careful work.

### The rest

- `frigate/util/file.py` — `FileLock` falls back to `msvcrt.locking` where
  `fcntl.flock` does not exist.
- `frigate/util/birdseye_pipe.py` — `TcpBirdseyePipe` preserves the two FIFO
  semantics that matter: frames with no reader are dropped, and a departed
  reader is detected on the next write.
- `frigate/util/go2rtc_process.py` — starts and stops go2rtc itself with
  `CREATE_NEW_PROCESS_GROUP`.
- `frigate/ffmpeg_presets.py` — `preset-windows-d3d11va`,
  `preset-windows-dxva2`, `preset-windows-qsv-h264/h265` for decode;
  `preset-windows-nvenc` and `preset-windows-qsv` for encode.
- `frigate/detectors/hardware.py` — enumerates Intel GPU/NPU via OpenVINO
  instead of DRM sysfs.
- `frigate/config/config.py` — Windows gets a CPU TFLite default model,
  downloaded atomically into `<base>\config\model_cache`.
- `frigate/log.py` — `os.register_at_fork` guarded behind `hasattr`.
- `frigate/util/ownership.py` — chown is a no-op.
- `docker/main/requirements-windows.txt` — a curated Windows dependency set
  (`ai-edge-litert` instead of `tflite_runtime`, `tensorflow-cpu`, no
  `setproctitle`/`memray`/`netifaces`).
- CI — `windows_native_smoke` job on `windows-latest`.
- Docs — a full Windows section in `docs/docs/frigate/installation.md:605`.

### Honest assessment

I scanned all of `frigate/` for POSIX-only constructs. Outside of tests, every
`/proc`, `/sys`, `/dev`, `os.geteuid`, `resource`, `pwd`, `fcntl` and
`os.mkfifo` use I found is either guarded by `IS_WINDOWS`, behind a
`try/except ImportError` at module scope, or inside a Linux-only code path.
That is a much better result than most Linux-first projects achieve.

**The problem is no longer "does the code import on Windows." It is "what does
the code do once it runs."**

---

## Part 2 — Findings

### P0-1. `restart_frigate()` hard-kills instead of shutting down

**Status: fixed.** `restart_frigate()` now calls `signal.raise_signal(SIGINT)` on Windows (`frigate/util/services.py`). Covered by `test_restart_frigate.test_windows_raises_the_signal_instead_of_killing`.

`frigate/util/services.py:47-52`

```python
def restart_frigate():
    if IS_WINDOWS or not hasattr(signal, "SIGINT"):
        # no supervisor outside of the container: exiting is the closest
        # equivalent, and a launcher restarts the application if asked to
        os.kill(os.getpid(), signal.SIGINT)
        return
```

On Windows, `os.kill` only special-cases `CTRL_C_EVENT` and `CTRL_BREAK_EVENT`.
Per the CPython docs, *"any other value for sig will cause the process to be
unconditionally killed by the `TerminateProcess` API, and the exit code will be
set to sig."* [1](https://docs.python.org/3/library/os.html#os.kill)

So the Windows branch does not "exit" — it calls `TerminateProcess(self, 2)`:
no `stop_event`, no `finally` blocks, no DB flush, no ffmpeg cleanup, no go2rtc
shutdown, and every child process orphaned. The intent of the branch is right;
the mechanism silently does something much more violent.

Note the same pattern at `services.py:67` and the unit test at
`frigate/test/test_restart_frigate.py:53`, which asserts `os.kill(99, SIGINT)`.
The test passes on Linux and would encode the wrong behaviour for Windows.

**Fix.** Use `signal.raise_signal(signal.SIGINT)` (Python 3.8+), which invokes
the registered Python-level handler on every platform. Keep `os.kill` for the
POSIX path only. Then make `test_restart_frigate.py` patch `IS_WINDOWS` and
assert `raise_signal` is called, so the branch is actually covered.

---

### P0-2. ffmpeg processes are hard-killed

**Status: fixed.** The new `request_ffmpeg_stop()` (`frigate/util/ffmpeg.py`) asks ffmpeg to stop before anything kills it: the `q` command on stdin, then a `CTRL_BREAK_EVENT` into the process group, then `terminate()` as the backstop. Every branch is covered in `frigate/test/test_ffmpeg_stop.py`.

`frigate/util/ffmpeg.py:13` (`stop_ffmpeg`), `:30` (`terminate_ffmpeg_stream`),
`frigate/output/camera.py:80` and `frigate/output/birdseye.py:227`
(`.exit()`).

All of them call `Popen.terminate()`. On POSIX that is `SIGTERM`; **on Windows
`Popen.terminate()` is `TerminateProcess`** — an uncatchable, immediate kill.

Consequences, in order of how much users will notice:

1. **The in-progress recording segment is never finalized.** Recordings are
   `-f segment` MP4 output (`frigate/config/camera/camera.py:303`) with a 10s
   default segment time. An MP4's `moov` atom is written when the segment is
   closed. `TerminateProcess` means the last segment is a truncated file with no
   `moov` — unplayable. The record maintainer does discard unreadable segments,
   so this degrades to "you silently lose up to one segment per stop" rather
   than "your library is corrupt," but every restart and every camera
   re-configure costs you recording.
2. **The graceful path is dead code.** `stop_ffmpeg` logs "Waiting for ffmpeg to
   exit gracefully" and then waits up to 30s. On Windows the process is already
   gone, so the `TimeoutExpired` → `kill()` branch never runs and the log is
   misleading.
3. **Exports and timelapses** (`frigate/record/export.py`) go through the same
   path, so a cancelled or restarted export can leave a half-written MP4 in
   `media\exports\staging`.

**Fix.** Stop ffmpeg the way ffmpeg expects to be stopped. Two viable options:

- **Write `q` to stdin** (preferred). ffmpeg treats `q` on stdin as "finish the
  current output and exit" — it flushes, finalizes the container, and exits 0.
  This works identically on every platform, so there is no Windows-specific
  branch to maintain. It needs `stdin=sp.PIPE` rather than `stdin=sp.DEVNULL`
  in `start_or_restart_ffmpeg()` (`ffmpeg.py:57-72`); the birdseye/camera
  output processes already use `PIPE`, so only the `frame_size is None` branch
  changes.
- **Generate `CTRL_BREAK_EVENT` into the process group.** Frigate already
  passes `start_new_session=True`, which on Windows is `CREATE_NEW_PROCESS_GROUP`,
  so `os.kill(pgid, signal.CTRL_BREAK_EVENT)` reaches the ffmpeg process and
  ffmpeg's console control handler exits cleanly. This requires no stdin change
  but only works for processes sharing the console — it will not work once
  Frigate runs as a service (see [P3-1](#p3-1-no-windows-packaging-or-service-story)).

Recommend `q` on stdin as the primary, with `CTRL_BREAK_EVENT` as the fallback
after a short timeout, and `TerminateProcess` only as the last resort. Regardless
of which you pick, `stop_ffmpeg` should branch on `IS_WINDOWS` so the log
messages match reality.

---

### P0-3. Child processes are orphaned on exit

**Status: fixed.** The new `frigate/util/job_object.py` puts Frigate into a Windows job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, installed before anything is spawned. Layout and opt-out are covered in `frigate/test/test_job_object.py`; the orphan check itself is in the end-to-end test.

On Linux, s6 (or a process-group kill) reaps everything Frigate spawned. On
Windows there is no such mechanism, and `start_new_session=True` maps to
`CREATE_NEW_PROCESS_GROUP`, which creates a group but does not tie its lifetime
to the parent's.

So when Frigate exits — cleanly or not — `ffmpeg.exe` instances, `go2rtc.exe`
and detector processes keep running. The damage compounds:

- The next start fails to bind the deterministic IPC ports
  (`platform.py:681`, base 5600) and the birdseye port (5620), because the
  orphans still hold them. Ports derived from names means the conflict is
  deterministic and permanent until the orphans are killed.
- Duplicate camera connections: two ffmpeg processes pulling the same RTSP
  stream, which most cameras will reject or throttle.
- Open file handles on recording segments in `cache\`, which turns the
  maintainer's move step into a `PermissionError` on Windows.

**Fix.** Two complementary pieces:

1. **A Job Object.** Assign every child to a job with
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. When the parent's handle closes —
   including when the process crashes or is killed — Windows terminates the
   whole job. This is the only approach that survives an abnormal exit, and it
   is what mature Windows daemons do. `pywin32` exposes it, or it can be done
   with `ctypes` in ~40 lines.
2. **An explicit PID registry.** Keep a set of spawned PIDs and terminate them
   in a `finally` / `atexit` handler, as a belt-and-braces measure for the
   normal-shutdown path. Add a Windows-only startup sweep on the known IPC
   ports that logs a clear "port 5600 is held by PID N (frigate orphan?)"
   message instead of a bare `OSError`.

---

### P0-4. Child processes do not handle `SIGBREAK`

**Status: fixed.** `frigate/service_manager/multiprocessing.py` now registers the same handler for `SIGBREAK` where it exists, alongside `SIGTERM` and `SIGINT`.

`frigate/service_manager/multiprocessing.py:155-156`

```python
signal.signal(signal.SIGTERM, receiveSignal)
signal.signal(signal.SIGINT, receiveSignal)
```

On Windows, `SIGTERM` can be registered but can never be delivered, so that
handler is inert. `SIGBREAK` — which is exactly what closing the console window
raises, and what the docs promise works ("Closing the console window also stops
it", `installation.md:641`) — is not handled at all in children.

Net effect: close the console and the parent's `stop_event` is set, but the
children die via the default handler without running their cleanup, leaving
exactly the orphans described in [P0-3](#p0-3-child-processes-are-orphaned-on-exit).

**Fix.** Route `SIGBREAK` through the same `receiveSignal`, mirroring what
`install_signal_handlers()` (`platform.py:630`) already does for the parent.
Better: have `service_manager` call `install_signal_handlers()` so there is one
implementation rather than two that can drift.

---

### P1-1. `hwaccel_args: auto` can never detect Windows hardware

`frigate/util/services.py:1362`

`auto_detect_hwaccel()` polls `http://127.0.0.1:1984/api/ffmpeg/hardware` and
returns `preset-nvidia` or `preset-vaapi`, else `""`. Two independent problems
on Windows:

- **Ordering.** `auto` is resolved during `FrigateConfig.load()`, which happens
  in `__main__.main()` *before* `FrigateApp.start()`. Frigate starts go2rtc
  itself at `frigate/app.py:688`. In the container this works because s6 starts
  go2rtc first; natively, go2rtc is not running yet, so the request fails, the
  exception is swallowed, and auto-detect returns `""` with a
  "using a GPU is highly recommended" warning. On a clean Windows install this
  is *always* the outcome, on *every* start.
- **Coverage.** Even with go2rtc up, only `cuda` and `vaapi` are checked.
  `d3d11va`, `dxva2` and `qsv` are never considered, so a Windows user with a
  perfectly capable iGPU gets software decode and a warning telling them to buy
  hardware they already have.

**Fix.**

- Move (or duplicate) the hwaccel probe to after `init_go2rtc()`, or make
  `auto` resolve lazily and re-resolve once go2rtc is up.
- Teach `auto_detect_hwaccel()` about the Windows sources go2rtc reports, and
  return `preset-windows-d3d11va` / `preset-windows-qsv-h264` as appropriate.
- On Windows, when no go2rtc is reachable at all, log "start go2rtc and set
  `ffmpeg.hwaccel_args` explicitly" rather than the current generic GPU warning,
  which is actively confusing on a machine with a GPU.

---

### P1-2. The docs recommend a preset that does not exist for decode

`docs/docs/frigate/installation.md:689` tells Windows users that NVENC is
available via `ffmpeg.hwaccel_args: preset-windows-nvenc`.

`preset-windows-nvenc` exists only in `PRESETS_HW_ACCEL_ENCODE_BIRDSEYE`
(`ffmpeg_presets.py:208`) and `PRESETS_HW_ACCEL_ENCODE_TIMELAPSE` (`:248`). It
is **not** in `PRESETS_HW_ACCEL_DECODE`. So
`parse_preset_hardware_acceleration_decode()` (`:270`) returns `None`, and
`camera.py:340` falls back to passing the raw string — ffmpeg receives
`preset-windows-nvenc` as a bare argument and refuses to start. A user following
the docs gets a camera that never comes up, with an error message that points
nowhere near the docs.

Same class of issue for `preset-windows-qsv` (encode-only).

**Fix, two parts:**

1. Add real Windows entries to `PRESETS_HW_ACCEL_DECODE` —
   `preset-windows-nvenc` → `-hwaccel cuda -hwaccel_output_format cuda`, and a
   codec-agnostic `preset-windows-qsv`.
2. Add a config validator that rejects any `hwaccel_args` / `input_args` /
   `output_args` starting with `preset-` that is not in the relevant map. Right
   now an unknown preset is a silent pass-through into a broken ffmpeg command
   line; it should be a config validation error naming the valid presets.

---

### P1-3. The hwaccel recommender can emit invalid Linux presets on Windows

`frigate/util/hwaccel.py`

`DECODE_HARDWARE` (`:56`) includes `openvino:GPU`, and on Windows
`detect_intel_gpu()` (`detectors/hardware.py:165`) *does* return
`openvino:GPU` via OpenVINO. So `hwaccel_options()` walks that path and lands on
`FAMILY_QSV` → `preset-intel-qsv-h264`, which is the **Linux** preset:

```
-hwaccel qsv -qsv_device {3} -hwaccel_output_format qsv -c:v h264_qsv
```

`{3}` comes from `_gpu_selector.get_gpu_arg()`, and `LibvaGpuSelector` returns
`[]` on Windows (`ffmpeg_presets.py:40-43`), so `get_gpu_arg` returns `""` and
the assembled command contains a bare `-qsv_device` with no value. Invalid
ffmpeg invocation.

Compounding it, `_intel_generation()` (`:124`) reads `/proc/cpuinfo`, which is
empty on Windows, so the vaapi-vs-qsv generation logic silently takes its
fallback path.

`recommend_hwaccel()` currently has no callers, so this is latent rather than
live — but it is a landmine for whoever wires it up.

**Fix.** Add Windows families (`FAMILY_WINDOWS_D3D11VA`,
`FAMILY_WINDOWS_QSV`) and short-circuit `hwaccel_options()` on `IS_WINDOWS`
before touching `PROC_ROOT`/`SYS_ROOT`. Make `_intel_generation()` read
`platform.processor()` or WMI on Windows.

---

### P2-1. Windows reserved device names are valid camera names

`frigate/const.py:150`

```python
REGEX_CAMERA_NAME = r"^[a-zA-Z0-9_-]+$"
```

That permits `con`, `aux`, `nul`, `prn`, `com1`–`com9`, `lpt1`–`lpt9`. A camera
named `con` makes Frigate try to create `...\media\clips\con` and write
`...\cache\con@2024...mp4`. Windows resolves those to device names, not files,
and the resulting errors (`FileNotFoundError` on a path that visibly exists) are
near-impossible for a user to diagnose.

**Fix.** Reject the reserved set in the camera-name validator, conditioned on
`IS_WINDOWS` so Linux users are unaffected. Put it in the validator rather than
at path-build time so the error surfaces at config load with a clear message.

---

### P2-2. Path length

Windows' historical `MAX_PATH` is 260 characters. Recording paths are
`<base>\media\recordings\<date>\<hour>\<camera>\<time>.mp4`, and export staging
nests further. A long `FRIGATE_BASE_DIR` plus a long camera name plus the date
tree can exceed it, producing `OSError` at write time — long after startup
succeeded.

**Fix.** Either prefix constructed absolute paths with `\\?\` (which requires
the path to be fully normalized and absolute — `platform.py` already guarantees
that), or validate the base directory length at startup and warn. At minimum,
document enabling `LongPathsEnabled`. The `\\?\` route is more robust but needs
care: it also disables the automatic forward-slash and `.`/`..` normalization
the Win32 path layer normally does, so every path must already be canonical.

---

### P2-3. Case-insensitive filesystem

`Driveway` and `driveway` are the same directory on NTFS. Any code that keys a
dict by camera name, or deduplicates paths via `realpath`, can behave
differently than on ext4.

**Fix.** On Windows, validate camera names case-insensitively and reject a new
camera whose name differs from an existing one only by case. Cheap, and prevents
a confusing class of bug.

---

### P2-4. Antivirus and indexing hold files open

The record maintainer moves segments from `cache\` to `media\recordings\`.
Windows Defender's real-time scanning (and the search indexer) opens each new
`.mp4` as it appears, so the move can race the scanner and fail with
`PermissionError`. The same applies to `frigate.db` and to `FileLock`'s lock
files.

**Fix.** Add small `robust_rename` / `robust_unlink` helpers that retry with
short exponential backoff, and use them for the move in the record maintainer
and for cleanup paths. Document adding Defender exclusions for the Frigate base
directory — this is the single highest-impact tuning tip for Windows users and
belongs in the install docs.

---

### P2-5. `FileLock` on Windows needs a real cross-process test

**Status: fixed.** `frigate/test/test_file.py::TestFileLockAcrossProcesses`
spawns a separate interpreter for every scenario: a held lock is denied to
another process, a released lock is takeable by another process, and a holder
killed without releasing (the crash case) does not keep the lock. Every test
runs the same way on Linux and Windows, so CI exercises `flock` and
`msvcrt.locking` through the same three assertions.

`frigate/util/file.py:315-345`

The `msvcrt.locking` fallback is correct in principle, but Windows locking
semantics differ from `flock` in ways that matter here: the lock is on a byte
range rather than the file, it is released when *any* handle to the file closes
(not just the locking one), and `msvcrt.locking` raises `OSError` on contention
rather than returning a status. `_try_lock()` returns `True` unconditionally
after the call, so whether contention surfaces as `False` or as an exception
depends on the caller's error handling.

**Fix.** Add a Windows CI test that attempts to take the lock from a genuinely
separate `python.exe` process (not just a second handle in-process), including
the "holder died" stale-lock case. This is exactly the kind of code that looks
fine on Linux and quietly fails to serialize on Windows.

---

### P2-6. `shlex.split(posix=True)` mangles unquoted Windows paths

`frigate/util/platform.py:413` (`split_ffmpeg_command`),
`frigate/util/builtin.py:139-141` (`get_ffmpeg_arg_list`).

`shlex` in POSIX mode treats `\` as an escape character. Verified:

```
shlex.split(r'-i D:\videos\cam.mp4')         → ['-i', 'D:videoscam.mp4']
shlex.split(r'-i "D:\videos\cam.mp4"')       → ['-i', 'D:\\videos\\cam.mp4']   # fine
```

So a user who sets `ffmpeg.input_args: -re -i D:\videos\cam.mp4` gets a silently
corrupted path and a camera that never connects. Quoted paths survive; unquoted
ones do not. The config schema already accepts `list[str]` for these fields
(`frigate/config/camera/ffmpeg.py:72`).

**Fix.** Use `posix=not IS_WINDOWS` in both helpers, and steer Windows users to
the YAML list form:

```yaml
ffmpeg:
  input_args:
    - -re
    - -i
    - D:\videos\cam.mp4
```

---

### P2-7. Timezone and DST across a long-lived process

**Status: fixed (documented).** The Windows CRT resolves the per-call DST
status from the zone's rules, so ordinary DST transitions update the offset in
segment names on their own. What goes stale is a change to the *time zone
setting* in a long-lived process, and `docs/docs/frigate/installation.md` now
says to restart Frigate after changing it.

`tzdata` and `tzlocal` are in the Windows requirements, so offset resolution
works. But `time.tzset()` does not exist on Windows, so a process started
before a DST transition keeps the old offset for its lifetime.
`CACHE_SEGMENT_FORMAT` (`const.py:168`) embeds `%z` in segment filenames, so
segments written after the transition carry a stale offset.

**Fix.** Either re-resolve the offset when building each segment name, or
document that Frigate should be restarted across DST changes on Windows. The
former is more correct; the latter is one line of docs.

---

### P2-8. Smaller items

- **`find_go2rtc()` uses `os.access(X_OK)`** (`go2rtc_process.py:47`). On
  Windows `X_OK` is derived from the file extension, not a permission bit, so
  this gives false negatives for a `.exe` with an unusual extension and false
  positives otherwise. Prefer a suffix check or `shutil.which`.
- **`get_physical_interfaces()`** (`services.py:245`) returns every adapter
  `psutil` knows about on Windows, including Hyper-V, VPN and loopback
  pseudo-interfaces. **Fixed:** the Windows branch now drops adapters whose
  name marks them virtual (vEthernet, VPN/tunnel, Bluetooth, WAN Miniport and
  friends) and adapters that are not up, keeping only the configured prefixes.
- **`frigate/util/hwaccel.py` has no `IS_WINDOWS` guard** at all. Every read
  funnels through `_read()` and fails soft, so it will not crash — but it will
  do pointless work and return Linux-shaped answers. An early return in
  `hwaccel_options()` is cheap.
- **`SHM_STATS_KEY = "/dev/shm"`** (`const.py:47`) is deliberately reused as a
  UI-facing storage key on Windows so the web UI need not care. **Fixed:** the
  storage panel now labels the tile "Page file (shared memory)" from the
  stats' `page_file_backed` flag and skips the raise-the-SHM-size warning
  there, and the backend notice is suppressed for page-file-backed memory.
- **`requirements-windows.txt` has drifted from `requirements-wheels.txt`** and
  is maintained by hand. A diff of the two shows `opencv-python-headless`,
  `netifaces`, `memray`, `setproctitle`, `mypy`, `types-requests` present only in
  wheels; `ai-edge-litert`, `sqlite-vec` only in Windows; and `py3nvml` pinned
  to PyPI in one and a git URL in the other. Most of that is intentional — but
  nothing enforces it. A CI check that reports drift would keep it honest.

---

### P3-1. No Windows packaging or service story

This is the biggest *adoption* gap, and it is not a code problem.

`installation.md:617-641` asks the user to: clone the repo, create a venv,
`pip install` twice, `npm ci`, `npm run build:native`, and then run
`python -m frigate` from a PowerShell window they must leave open. There is no
installer, no bundled ffmpeg or go2rtc, no service, no autostart, and no
supervisor — the docs say so plainly: *"Native Windows does not install a
Windows service or automatic restart supervisor. If Frigate exits or you use the
restart action, launch it again from PowerShell."*

For an NVR — a thing people expect to run unattended for months — "leave a
console window open" is not a shippable answer.

Suggested sequence, cheapest first:

1. **A bootstrap script.** `frigate-windows.ps1` that installs the Python
   requirements, fetches pinned + checksummed Windows builds of ffmpeg and
   go2rtc, builds the web UI, and creates a start-menu shortcut. Note that
   `frigate/util/runtime_deps.py` already implements exactly this pattern for
   accelerator SDKs (manifest of pinned, checksummed artifacts, fetched into a
   cache dir, with an offline pre-seed path) — reuse it rather than writing new
   download code.
2. **Ship a prebuilt `web/dist`.** Requiring Node 22 + `npm ci` just to run an
   NVR is a large, unnecessary barrier. Publish a release artifact with the
   frontend already built.
3. **A Windows service.** NSSM, or a small `pywin32` service, so Frigate starts
   at boot and runs with no console. **This interacts with everything in P0:**
   a service runs in session 0 with no console, so `CTRL_C_EVENT`,
   `CTRL_BREAK_EVENT` and `SIGBREAK` are all unavailable. Shutdown arrives via
   the service control manager instead. `install_signal_handlers()` needs a
   service-aware branch, and the graceful-ffmpeg-stop work in
   [P0-2](#p0-2-ffmpeg-processes-are-hard-killed) must not depend on console
   events — which is the main reason to prefer `q` on stdin over
   `CTRL_BREAK_EVENT` there.
4. **A winget manifest**, so `winget install Frigate` works.

---

### P3-2. Windows CI is a thin allowlist

`.github/workflows/pull_request.yml:81-119` installs the Windows requirements,
builds the web UI, and runs **6 hand-picked tests** plus `--validate-config`.
The repo has 121 test files.

An allowlist rots: every new test is Linux-only by default, and nothing tells
the author otherwise. It also means the current Windows confidence is
"these six things work," not "Frigate works."

Suggestions, in order of value:

1. **Invert it.** Run the whole unit suite on `windows-latest`, keep a
   *denylist* of known-failing tests with a reason next to each. New failures
   show up immediately; the denylist becomes a visible, shrinkable backlog.
   Expect real findings here — `test_mqtt_network_loop.py` imports `fcntl` and
   `resource` at module scope, and many tests assert container paths like
   `/media/frigate/exports`.
2. **Add an end-to-end Windows job** that starts Frigate against a synthetic
   camera (`ffmpeg -f lavfi -i testsrc`, per
   `docs/docs/troubleshooting/dummy-camera.md`), waits for a recording segment
   to appear in `media\recordings`, and hits `/api/config` and the live
   endpoint. This is the only test that would have caught P0-1 through P0-4.
3. **Run `mypy` and `ruff` on Windows too.** The `platform.py` guards are easy
   to get subtly wrong, and type checking is where "this can be `None` on some
   platforms" bugs surface.
4. **Add the requirements drift check** from P2-8.
5. **Add a service-mode job** once P3-1(3) lands — install and stop via `sc.exe`
   and assert a clean shutdown, since that path has no console signals at all.

---

### P3-3. Detector coverage on Windows is narrow

Today: CPU TFLite (via `ai-edge-litert`), ONNX Runtime CPU, and OpenVINO
CPU/GPU/NPU. The docs are honest about the rest, which is good.

Two cheap wins:

- **`onnxruntime-directml`.** DirectML gives GPU inference across AMD, Intel and
  Nvidia on Windows from one runtime. For a Windows user without an Intel chip
  this is a large improvement over CPU inference, and it is a single dependency
  swap. Worth testing and documenting as the recommended default.
- **Fail loudly at config validation.** If a user configures Coral, Hailo or
  another Linux-only accelerator on Windows, they should get a validation error
  naming the platform requirement, not a crash when the detector process starts.

---

## Part 3 — Suggested order of work

| # | Item | Why here |
| --- | --- | --- |
| ~~1~~ | ~~[P0-1](#p0-1-restart_frigate-hard-kills-instead-of-shutting-down) `raise_signal`~~ | **done** |
| ~~2~~ | ~~[P0-2](#p0-2-ffmpeg-processes-are-hard-killed) graceful ffmpeg stop~~ | **done** |
| ~~3~~ | ~~[P0-3](#p0-3-child-processes-are-orphaned-on-exit) Job Object~~ | **done** |
| ~~4~~ | ~~[P0-4](#p0-4-child-processes-do-not-handle-sigbreak) `SIGBREAK` in children~~ | **done** |
| ~~5~~ | ~~[P3-2](#p3-2-windows-ci-is-a-thin-allowlist)(1) run the full suite on Windows~~ | **done** (`windows_native_unit_suite`, denylist driven) |
| ~~6~~ | ~~[P1-2](#p1-2-the-docs-recommend-a-preset-that-does-not-exist-for-decode) preset validator + real Windows decode presets~~ | **done** |
| ~~7~~ | ~~[P1-1](#p1-1-hwaccel_args-auto-can-never-detect-windows-hardware) fix `auto` on Windows~~ | **done** |
| ~~8~~ | ~~[P2-1](#p2-1-windows-reserved-device-names-are-valid-camera-names) / [P2-2](#p2-2-path-length) / [P2-3](#p2-3-case-insensitive-filesystem) name + path validators~~ | **done** (path length warns at startup rather than rewriting paths) |
| ~~9~~ | ~~[P3-1](#p3-1-no-windows-packaging-or-service-story)(1)(2) bootstrap + prebuilt web~~ | **done** (`bootstrap_windows.py`, release bundle job) |
| ~~10~~ | ~~[P1-3](#p1-3-the-hwaccel-recommender-can-emit-invalid-linux-presets-on-windows) Windows hwaccel families~~ | **done** |
| ~~11~~ | ~~[P2-4](#p2-4-antivirus-and-indexing-hold-files-open) / [P2-5](#p2-5-filelock-on-windows-needs-a-real-cross-process-test) / [P2-6](#p2-6-shlexsplitposixtrue-mangles-unquoted-windows-paths) robustness~~ | **done** (P2-5 has cross-process tests on both platforms) |
| 12 | [P3-1](#p3-1-no-windows-packaging-or-service-story)(3) SCM service + winget | The launcher covers unattended operation; boot start and session 0 shutdown still need a service. **Winget manifest added** (`winget-manifest/Frigate.FrigateWindows.yaml`) as a starter. |
| 13 | [P3-3](#p3-3-detector-coverage-on-windows-is-narrow) DirectML | **Default enabled**: `bootstrap_windows.py` now defaults `--directml` to `True` with `--no-directml` to opt out. Making DirectML the recommended default needs a real Windows GPU validation run. |

## Part 4 — What is already right, and worth protecting

Worth saying explicitly so it does not get refactored away:

- **`platform.py` imports nothing from Frigate.** That constraint (forced by
  `const.py` importing it) is what keeps the abstraction honest. Keep it.
- **Whole-directory relocation via `FRIGATE_BASE_DIR`.** "Put recordings on
  D:" is the single most common Windows NVR need and it works today.
- **`resolve_media_url_path()` / `is_subpath()`.** The drive-letter `ValueError`
  catch is the kind of detail that only shows up in review, and it is there.
- **`vod_native.py` and `web.py` as an nginx facade.** Reimplementing nginx's
  routing *and* its `auth_request` semantics in-process, and documenting why the
  HTTP-subrequest approach deadlocks, is careful engineering.
- **The honest docs.** `installation.md:605-700` states plainly what does not
  work (no service, no Coral/Hailo, no `/dev/shm` accounting, page-file-backed
  shared memory). That is more useful than optimistic docs, and it is the main
  reason the gaps above are enumerable at all.

---

## Part 5 — What is still unverified

Everything above was written and checked from Linux. That bounds what the
changes below actually guarantee, and it is worth being precise about it.

**Verified here** (20 unit tests, `ruff` clean):

- every branch of `request_ffmpeg_stop()` — POSIX terminate, Windows `q` on
  stdin, the break-event fallback, and each fallback to `terminate()`
- `restart_frigate()` on the Windows path now calls `signal.raise_signal`
  rather than `os.kill`
- the ctypes layout of the job object structures, byte for byte
  (this caught a real bug: `wintypes.DWORD` is `c_ulong`, which is 4 bytes on
  Windows and 8 on Linux, so the struct was 8 bytes too large and every field
  after `LimitFlags` was misaligned. It works on Windows by accident; pinning
  `c_uint32` makes it correct and checkable everywhere)
- the opt-out path and the no-op path for the job object

**Not verified — needs one run on real Windows:**

- that the job object installs at all, and that it does not kill children
  early. `FRIGATE_NO_JOB_OBJECT=1` disables it if it misbehaves.
- that ffmpeg actually honours `q` on a piped stdin on Windows. If it does
  not, the break event or `terminate()` takes over and the behaviour is
  exactly what it is today, so this fails safe.
- that Frigate exits 0 after `/api/restart`. This is the assertion most likely
  to need loosening on a first run, and the failure message carries Frigate's
  own log tail so it should say why.
- the end-to-end config itself: `testsrc` through `-c:v libx264` into
  `-f segment`, whether the CPU TFLite model downloads and loads on the
  runner, and whether the first segment lands in `media/recordings` inside the
  180s budget.
- the bootstrap script end to end: the venv, pip, the pinned FFmpeg/go2rtc
  download and extraction into `%LOCALAPPDATA%\Frigate\config`, the launcher
  generation and the Start Menu shortcut. The unit tests cover the digest
  bookkeeping, extraction, layout and launcher text, but not a real download.
- the launcher's restart loop against a real restart: that the marker written
  by `restart_frigate()` is seen and consumed by `run_frigate.ps1`, and that
  Ctrl+C still ends the loop.
- DirectML inference on real AMD and Nvidia hardware: the provider pass
  through is unit tested, but the swap (`--directml`) has not run against a
  real GPU or driver.

The `windows_native_e2e` job uploads Frigate's log and media on failure, so
the first red run should be enough to fix any of these.

**One thing the end-to-end test deliberately does not cover:** the
service-mode shutdown path. A Windows service runs in session 0 with no
console, so `CTRL_BREAK_EVENT` and `SIGBREAK` do not exist there at all, and
neither does the console-close behaviour the docs describe. When
[P3-1](#p3-1-no-windows-packaging-or-service-story)(3) is built,
`install_signal_handlers()` needs a service-aware branch and the graceful
ffmpeg stop has to keep working without console events — which is why `q` on
stdin is the primary mechanism rather than the break event.
