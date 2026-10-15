# Windows Compatibility

The audit and current state of running Frigate natively on Windows, with no Docker and no WSL. Setup, ports and tuning are user documentation and live in [the Windows install guide](docs/docs/frigate/installation.md#windows); this file is for whoever maintains the port. File and function references are against the current tree, and the original audit's full analysis of each finding is in this file's git history.

## Summary

The port runs. The four process-lifecycle bugs the first audit found are fixed and unit tested, the install is one command, the whole unit suite plus an end-to-end job run on `windows-latest`, and GPU inference is available across vendors. What has not happened yet is a validation pass on real Windows hardware: the fixes were written and tested from Linux, and the Windows CI jobs are the only thing that has executed them.

## Findings

| ID | Finding | Status | Where it is handled |
| --- | --- | --- | --- |
| P0-1 | `restart_frigate()` killed the process instead of shutting it down | fixed | `frigate/util/services.py`: `_stop_self()` calls `signal.raise_signal(SIGINT)`, because `os.kill(pid, SIGINT)` is `TerminateProcess` on Windows. `frigate/test/test_restart_frigate.py` |
| P0-2 | ffmpeg was terminated, so the segment being written was never finalized | fixed | `request_ffmpeg_stop()` in `frigate/util/ffmpeg.py`: quit on stdin, then a break event into the process group, then `terminate()`. `frigate/test/test_ffmpeg_stop.py` |
| P0-3 | ffmpeg, go2rtc and detector processes outlived Frigate | fixed | `frigate/util/job_object.py` puts Frigate in a job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` before anything is spawned; `FRIGATE_NO_JOB_OBJECT=1` opts out. `frigate/test/test_job_object.py` |
| P0-4 | children ignored `SIGBREAK`, so closing the console orphaned them | fixed | `frigate/service_manager/multiprocessing.py` registers `SIGBREAK` alongside `SIGINT` where the platform has it |
| P1-1 | `hwaccel_args: auto` could never detect Windows hardware | fixed | go2rtc's decoder list first, display adapters as the fallback, returning a `preset-windows-*` preset; software decode with a warning when neither finds a GPU |
| P1-2 | the docs recommended a decode preset that did not exist | fixed | real Windows decode presets in `frigate/ffmpeg_presets.py`, and an unknown or Linux-only preset is now a config validation error naming the valid ones |
| P1-3 | the hwaccel recommender emitted Linux presets | fixed | Windows families in `frigate/util/hwaccel.py`; `_intel_generation()` reads the CPU brand from the registry instead of `/proc/cpuinfo` |
| P1-4 | audio shutdown waits multiplied by camera count, and a blocked log-pipe thread could pin the worker | fixed | Windows audio maintainers share an 11s deadline, use a nonblocking FFmpeg quit poll, and use daemon log-pipe threads; the parent waits at most 13s. Linux retains its per-thread cleanup. `frigate/test/test_audio_spawn.py` and `frigate/test/test_ffmpeg_stop.py` |
| P1-5 | an SCM stop during startup or the restart delay could launch Frigate after STOP was requested | fixed | `SvcDoRun()` checks the sticky stop flag before each launch and consumes restart markers after a service stop; regressions in `frigate/test/test_windows_service.py` |
| P2-1 | reserved device names were valid camera names | fixed | rejected on Windows only (`WINDOWS_RESERVED_NAMES`, `frigate/config/camera/camera.py`) |
| P2-2 | the 260 character path limit | mitigated | `long_path_warning()` warns at startup with the base directory and the registry value. Paths are not rewritten |
| P2-3 | the filesystem is case insensitive | fixed | camera names that differ only by case are rejected on Windows |
| P2-4 | antivirus and the indexer hold recording files open | fixed | `robust_remove()` / `robust_move()` retry with backoff, used by the record maintainer, the output process and preview frames; Defender exclusions are in the install docs |
| P2-5 | `FileLock` had no cross-process test | fixed | `TestFileLockAcrossProcesses` in `frigate/test/test_file.py` runs held, released and killed-holder cases against a separate interpreter on both platforms |
| P2-6 | `shlex.split(posix=True)` mangled unquoted Windows paths | fixed | `split_ffmpeg_command()` uses a Windows splitter; POSIX behaviour is unchanged |
| P2-7 | time zone changes go stale in a long-lived process | documented | ordinary DST transitions resolve themselves. The docs say to restart Frigate after changing the time zone, which is the case that actually goes stale |
| P2-8 | smaller items | fixed | `find_go2rtc()` skips the `X_OK` check on Windows, `hwaccel.py` guards its Linux probes, `get_physical_interfaces()` drops virtual and down adapters, the storage panel labels page-file-backed memory instead of `/dev/shm`, and `testing-scripts/check_windows_requirements.py` reports requirement drift |
| P3-1 | no installer, no bundled binaries, no service | done, service unverified | `bootstrap_windows.py`, the `windows_bundle` job in `release.yml`, `frigate/util/windows_service.py` with `scripts/install_frigate_service.ps1`, and a starter `winget-manifest/Frigate.FrigateWindows.yaml` |
| P3-2 | Windows CI was six hand-picked tests | done | `windows_native_smoke`, `windows_native_e2e` and `windows_native_unit_suite` in `.github/workflows/pull_request.yml`, the last driven by `frigate/test/windows_suite_denylist.txt` |
| P3-3 | detector coverage was CPU and Intel only | partly | `onnxruntime-directml` covers AMD, Intel and Nvidia and is installed by default; Windows rejects non-Windows detector types even when their plugin imports, and refuses their runtime manifests before installation. Real-GPU validation remains open |

## What the port consists of

### The platform layer

`frigate/util/platform.py` is the keystone. Its rules, which the rest of the port depends on: import nothing from Frigate outside the module (because `frigate.const` imports it and nearly everything imports `frigate.const`), never import a POSIX-only module at module scope, and return every path as an absolute OS-native path.

| Concern | How it is handled |
| --- | --- |
| Platform detection | `IS_WINDOWS` / `IS_LINUX` / `IS_MACOS` |
| Directory layout | One base dir (`%LOCALAPPDATA%\Frigate` by default), overridable per directory with `FRIGATE_BASE_DIR`, `FRIGATE_CONFIG_DIR`, `FRIGATE_MEDIA_DIR`, `FRIGATE_CACHE_DIR`, `FRIGATE_TMP_DIR` |
| Binary discovery | `find_executable()` checks `PATH`, then the install dir, the bundled ffmpeg dir, and the winget/chocolatey/scoop shim dirs; `EXECUTABLE_SUFFIX` appends `.exe` |
| Process priority | `os.nice()` becomes `psutil` priority classes |
| Signals | `install_signal_handlers()` installs `SIGINT` + `SIGBREAK` on Windows, `SIGINT` + `SIGTERM` on POSIX |
| IPC | Unix sockets become loopback TCP on ports derived from the endpoint name, base 5600, movable with `FRIGATE_IPC_PORT_BASE` |
| Birdseye restream | FIFO becomes the loopback TCP listener in `frigate/util/birdseye_pipe.py` |
| Shared memory | No `/dev/shm` accounting; page-file backed, and `get_shared_memory_total_mb()` returns `None` |
| Media URLs | `to_media_url_path()` / `resolve_media_url_path()` map `D:\Frigate\media\clips\...` to `/clips/...` so the browser never learns the real layout |
| Path safety | `is_subpath()` catches the `ValueError` that `commonpath` raises across drives |
| ffmpeg | `ffmpeg_stdin_input()` becomes `pipe:0`; `ffmpeg_concat_file_line()` normalizes backslashes, which the concat demuxer treats as escapes |
| Path limit | `long_path_warning()` measures the deepest recording path against `MAX_PATH` |

### The nginx replacement

`frigate/api/web.py` and `frigate/api/vod_native.py` rebuild the container's nginx facade in front of the same FastAPI app: the static web app, media mounts, `/vod/*` HLS built in Python, websocket and go2rtc proxying, and an in-process ASGI auth call instead of an HTTP subrequest. The last one is a real trap: a loopback auth request re-enters the server that is handling it and deadlocks a single-threaded server.

### The rest

- `frigate/util/file.py`: `FileLock` falls back to `msvcrt.locking`, and `robust_remove` / `robust_move` retry around sharing violations.
- `frigate/util/job_object.py`: the job object that keeps children from outliving Frigate.
- `frigate/util/go2rtc_process.py`: starts and stops go2rtc itself, with `CREATE_NEW_PROCESS_GROUP`.
- `frigate/util/go2rtc_config.py`: Frigate's generated birdseye source uses the resolved Windows FFmpeg binary and the TCP pipe; user-authored go2rtc stream sources are passed through after the existing exec-safety filter. No Linux command is generated for a custom source, so any OS-specific custom command/path remains the configuration owner's responsibility rather than being guessed or rewritten.
- `frigate/util/ffmpeg.py`: `ffmpeg_popen_kwargs()`, `stop_ffmpeg()` and `request_ffmpeg_stop()`.
- `frigate/app.py`: lets worker processes observe the shared stop event and close their own FFmpeg resources instead of terminating them during normal shutdown.
- `frigate/ffmpeg_presets.py`: `preset-windows-d3d11va`, `preset-windows-dxva2`, `preset-windows-qsv-h264`, `preset-windows-qsv-h265` for decode; `preset-windows-nvenc` and `preset-windows-qsv` for encode. Linux-only presets, recognized raw Linux/macOS hardware and V4L2 arguments across FFmpeg fields, and Linux device/Unix-socket camera inputs are rejected on Windows.
- `frigate/detectors/device.py` limits Windows configurations to CPU, ONNX, OpenVINO and ZMQ, even when a Linux-only plugin module imports; `frigate/detectors/detection_api.py` blocks its runtime manifest from being installed as a second line of defense.
- `frigate/detectors/hardware.py`: Windows probes only OpenVINO Intel GPU/NPU, NVIDIA, and CPU; Linux sysfs, device-tree and device-node probes are not run. `frigate/detectors/detection_runners.py` also skips the RKNN device-tree and LighterANE provider lookups when loading models on Windows.
- `frigate/stats/hardware.py`: skips hardware pollers without a Windows implementation and does not read Linux detector temperature sysfs paths on Windows.
- `frigate/config/config.py`: Windows gets a CPU TFLite default model, downloaded atomically into `<base>\config\model_cache`.
- `frigate/log.py`: no `os.register_at_fork`, and std streams are reopened for `spawn`.
- `frigate/util/ownership.py`: chown is a no-op, `pwd` is imported optionally.
- `docker/main/requirements-windows.txt`: a curated Windows dependency set (`ai-edge-litert` instead of `tflite_runtime`, `tensorflow-cpu`, `pywin32`, no `setproctitle` or `memray`).
- `bootstrap_windows.py`: stdlib-only installer that creates the venv, installs the requirements, fetches pinned FFmpeg 8.1.2 and go2rtc 1.9.14 with recorded digests, builds the web UI when Node is present, and writes `run_frigate.ps1` plus a Start Menu shortcut.

## Open items

1. **The SCM service has not yet been run under the SCM in CI.** The Windows E2E now has an opt-in (`FRIGATE_E2E_SCM=1`) test that installs the service, starts Frigate as LocalService, and asks the SCM to stop it; the named-event test and mocked wrapper tests remain separate checks. Registration, session 0 startup, service-account permissions, recovery actions and clean stop reporting need the first Windows run to confirm.
2. **The winget manifest needs a signed installer URL.** `winget-manifest/Frigate.FrigateWindows.yaml` is a starter.
3. **DirectML has not run on a real GPU.** The provider pass-through is unit tested; the package swap has not been validated against AMD or Nvidia drivers.
4. **`windows_native_unit_suite` is `continue-on-error`.** It reports rather than blocks until `frigate/test/windows_suite_denylist.txt` covers every test that genuinely cannot run on Windows. Dropping that flag is the signal the denylist is done.
5. **Some hardware telemetry has no Windows implementation.** Linux-only probes are skipped, while libva, DRM sysfs, Rockchip, Jetson, Hailo and Axiom stats remain unavailable on Windows.
6. **The path limit is reported, not solved.** `long_path_warning()` warns; nothing rewrites paths with the `\\?\` prefix.

## Upstream parity

This fork tracks `blakeblackshear/frigate` `dev` and syncs by re-applying upstream's changes by hand, so upstream commit shas are not this tree's ancestry. Tree content is what gets checked.

`testing-scripts/check_upstream_parity.py` compares `HEAD` against the upstream commit recorded in `testing-scripts/upstream_parity_baseline.json`, verified against `16a377233e3a1001775790ab64947cb347d6dfae` on 2026-10-15. It fails when upstream has a file this fork lacks, when a file diverges without a recorded reason, when upstream edits lines a Windows replacement covers, or when a recorded divergence disappears. Every entry carries a reason.

```bash
python testing-scripts/check_upstream_parity.py --fetch     # compare, fetching the baseline commit
python testing-scripts/check_upstream_parity.py --update    # re-baseline after reviewing
python testing-scripts/check_upstream_parity.py --show PATH # inspect the upstream lines not carried
python testing-scripts/check_upstream_parity.py --fetch --drift  # has upstream moved past the baseline
```

Two jobs run it: `Upstream Parity` on every pull request, against the baseline commit so the result does not depend on when the job runs, and `Upstream Drift` weekly in `.github/workflows/upstream_parity.yml`, so a missed sync is visible. After syncing, review each difference as an `IS_WINDOWS` branch or a deliberate replacement, then re-baseline.

The port adapts rather than removes upstream behaviour. Every replaced Linux call was checked for identical results on Linux: directory constants, the eight ZeroMQ `ipc://` endpoints, the storage-stat keys the UI reads, `resolve_ffmpeg_path()` against the image's bundled FFmpeg versions, the `/labelmap.txt` default, and the `openvino:CPU` first-run detector default.

## What is still unverified

The Windows CI jobs run on GitHub runners, which proves the code executes there and not that it works with every driver, camera and GPU. These need a run on real hardware:

- That the job object installs and does not kill children early. `FRIGATE_NO_JOB_OBJECT=1` disables it if it misbehaves.
- That ffmpeg honours `q` on a piped stdin on Windows. If it does not, the break event and then `terminate()` take over, so this fails safe.
- That Frigate exits 0 after `/api/restart`, which is the assertion most likely to need loosening on a first run.
- The end-to-end config itself: `testsrc` through `libx264` into `-f segment`, whether the CPU TFLite model downloads and loads, and whether the first segment lands in `media\recordings` inside the timeout.
- `bootstrap_windows.py` end to end: a real download and extraction into `%LOCALAPPDATA%\Frigate\config`, launcher generation and the Start Menu shortcut. The unit tests cover digests, extraction, layout and launcher text.
- The launcher's restart loop against a real restart, and that Ctrl+C still ends it.
- DirectML inference on real AMD and Nvidia hardware.

`windows_native_e2e` uploads Frigate's log and media on failure, so the first red run should say which of these it is.

## Worth protecting

Worth saying explicitly so it does not get refactored away:

- **`platform.py` imports nothing from Frigate.** That constraint is what keeps the abstraction honest.
- **Whole-directory relocation through `FRIGATE_BASE_DIR`.** "Put recordings on D:" is the most common Windows NVR need and it works today.
- **`resolve_media_url_path()` and `is_subpath()`.** The drive-letter `ValueError` catch is the kind of detail that only shows up in review.
- **The nginx facade doing auth in process,** with the deadlock reason written down.
- **Honest documentation.** The install guide states plainly what a native install does not support, which is the main reason the gaps above are enumerable at all.
