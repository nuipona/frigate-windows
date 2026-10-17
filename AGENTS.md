# Agent and contributor guide

This repository is the native Windows edition of Frigate. It is a
Windows-only application: 64-bit CPython 3.12, running directly on Windows
as a normal process or SCM service. There is no container, WSL, POSIX, or
Unix-service runtime, and changes must not reintroduce one. The full runtime
contract is in [WINDOWS_COMPATIBILITY.md](WINDOWS_COMPATIBILITY.md); setup
and review rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Runtime contract

- `frigate.__main__` rejects every platform other than Windows before
  runtime initialization (`frigate.util.platform.require_windows`).
- Multiprocessing always uses `spawn`.
- Writable data defaults to `%LOCALAPPDATA%\Frigate`, relocatable with
  `FRIGATE_BASE_DIR` and the `FRIGATE_*_DIR` overrides. Build paths with
  `pathlib` or `os.path`; never introduce Unix root paths, device nodes,
  Unix sockets, UID/GID ownership, or Unix service managers.
- Executables are native `.exe` files, located through
  `frigate.util.platform.find_executable`. The bootstrap installs pinned
  FFmpeg and go2rtc builds and verifies their SHA-256 digests.
- Process, IPC, shared-memory, priority, and service behavior goes through
  `frigate/util/platform.py` and `frigate/util/job_object.py` where
  practical. `platform.py` must remain importable during early startup
  without importing other Frigate modules.
- The public HTTP listener (default `0.0.0.0:8971`) is authenticated; the
  internal listener (`127.0.0.1:5000`) is loopback-only and trusted. Never
  trust client-supplied identity headers.
- Supported detectors are CPU TensorFlow Lite, ONNX Runtime (CPU or
  DirectML), OpenVINO, and ZMQ remote inference. Detector or acceleration
  additions must have a maintained native Windows runtime and CPython 3.12
  x64 wheels where Python packages are required.

## Required checks

From the repository root, on Windows:

```powershell
ruff format bootstrap_windows.py frigate migrations testing-scripts
ruff check bootstrap_windows.py frigate migrations testing-scripts
python -m compileall -q bootstrap_windows.py frigate migrations testing-scripts
python testing-scripts/check_windows_requirements.py
python testing-scripts/run_windows_unit_suite.py
```

PowerShell, SCM, Win32 process behavior, and the downloaded Windows
executables can only be validated on a Windows x64 host; results from other
operating systems are not evidence for this runtime. Tests that cannot pass
on Windows belong in `frigate/test/windows_suite_denylist.txt` with a
concrete reason, and the list is expected to shrink, never grow silently.

Regenerate derived artifacts when their sources change:

- API auth spec: `python generate_api_auth_spec.py` (verify with `--check`)
- Config translations: `python generate_config_translations.py`

## Web frontend

From `web`:

```powershell
npm ci
npm run lint
npm run build:native
```

- `npm run build` builds under a configurable base path; the native server
  serves `npm run build:native` output from `/`. The native build is what
  ships.
- All user-facing strings go through `react-i18next`. English strings live
  in `public/locales/en/`, organized by namespace; never hardcode display
  text in a component. Extract keys with `npm run i18n:extract`.
- UI primitives are Radix UI/shadcn components in `src/components/ui/`,
  styled with TailwindCSS and the `cn()` helper.
- TypeScript runs in strict mode; avoid `any`.
- `npm run build` output and `web/dist` are generated. Do not hand-edit
  generated files, Playwright outputs, or locale files beyond adding the
  English source strings for new keys.

## Pull requests

Open pull requests against `dev`. Keep each pull request focused and
describe the behavior, the tests run, and any hardware used for validation.
Every pull request must be read and submitted by a person; disclose AI
assistance in the pull request description.
