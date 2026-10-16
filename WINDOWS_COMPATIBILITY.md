# Native Windows Architecture

Frigate in this repository is a Windows-only application. It requires 64-bit
CPython 3.12 and runs directly on Windows; there is no alternate container,
WSL, POSIX, or Unix-service runtime.

## Runtime contract

- `frigate.__main__` rejects every platform other than Windows before runtime
  initialization.
- Multiprocessing always uses `spawn`.
- Writable data defaults to `%LOCALAPPDATA%\Frigate`. `FRIGATE_BASE_DIR`,
  `FRIGATE_CONFIG_DIR`, `FRIGATE_MEDIA_DIR`, `FRIGATE_CACHE_DIR`, and
  `FRIGATE_TMP_DIR` can relocate it, including to another drive.
- Executables are native `.exe` files. The bootstrap installs pinned FFmpeg
  8.1.2 and go2rtc 1.9.14 builds and verifies their SHA-256 digests.
- Named shared-memory mappings, loopback TCP IPC, Windows process groups,
  priority classes, service events, and a kill-on-close Job Object replace
  Unix-specific process facilities.
- Files inherit Windows ACLs. There is no UID/GID ownership layer.

`frigate/util/platform.py` is the central implementation of these rules. It is
kept independent of the rest of the package because `frigate.const` imports it
during early initialization.

## Web and service topology

`frigate/api/web.py` owns HTTP serving. The default listeners are:

- `0.0.0.0:8971`: authenticated UI, API, media, VOD, and live proxy traffic.
- `127.0.0.1:5000`: trusted internal media requests from Frigate's own jobs.

The two ports must be distinct. Authentication is evaluated in process and
caller-supplied identity headers are removed before requests enter the API.
Proxy identity mappings require a configured shared secret. Client-address
forwarding is accepted only when the direct peer belongs to a configured
trusted network.

Frigate launches and stops `go2rtc.exe` as a child. FFmpeg and other descendants
are attached to a Windows Job Object so they cannot outlive Frigate. The
optional SCM wrapper in `frigate/util/windows_service.py` uses the same runtime
and pins the child working directory to the source/install root.

## Supported hardware

Object detection is intentionally limited to backends with native Windows
runtimes:

- CPU TensorFlow Lite
- ONNX Runtime (CPU or DirectML)
- OpenVINO (CPU, Intel GPU, or Intel NPU)
- ZMQ remote inference

Video acceleration presets cover D3D11VA, DXVA2, Intel QSV, NVIDIA CUDA/NVENC,
AMD AMF, Vulkan, and software fallback. Hardware statistics use `psutil` and,
when installed and enabled, NVIDIA NVML.

Accelerators that require Unix device nodes or vendor Linux SDKs are not part
of the codebase or configuration schema.

## Installation and validation

Run from 64-bit CPython 3.12 on Windows:

```powershell
py -3.12 bootstrap_windows.py
.\run_frigate.ps1
```

Add `--install-service` from an elevated PowerShell session to register the SCM
service. See `docs/docs/frigate/installation.md` for paths, environment
overrides, service commands, upgrades, and uninstall steps.

Repository checks are:

```powershell
ruff format --check bootstrap_windows.py frigate migrations testing-scripts
ruff check bootstrap_windows.py frigate migrations testing-scripts
python -m compileall -q bootstrap_windows.py frigate migrations testing-scripts
python testing-scripts/check_windows_requirements.py
python testing-scripts/run_windows_unit_suite.py
cd web
npm ci
npm run lint
npm run build:native
```

PowerShell and real-device validation must run on Windows. Linux/container test
results are not accepted as evidence for this runtime.

These checks also run automatically in the `Windows Python` and `Windows Web`
GitHub Actions workflows on every pull request and push to `dev`.

## Release limitation

`winget-manifest/Frigate.FrigateWindows.yaml` remains a template until a signed
Windows installer is published. The bootstrap and source launcher are the
supported installation path in the meantime.
