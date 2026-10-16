# Contributing

This repository is the native Windows edition of Frigate. Changes must preserve
the Windows-only runtime contract: 64-bit CPython 3.12, `spawn`
multiprocessing, native Windows paths and executables, and no dependency on
WSL, containers, Unix sockets, device nodes, UID/GID ownership, or Unix service
managers.

Open pull requests against `dev`. Keep each pull request focused and describe
the behavior, tests, and any hardware that was used for validation.

## Local setup

Use 64-bit CPython 3.12 on Windows:

```powershell
py -3.12 bootstrap_windows.py --skip-shortcut
.\.venv\Scripts\Activate.ps1
```

The bootstrap creates the virtual environment, installs `requirements.txt`,
fetches verified Windows builds of FFmpeg and go2rtc, and builds the web UI when
Node.js is available.

## Required checks

From the repository root:

```powershell
ruff format bootstrap_windows.py frigate migrations testing-scripts
ruff check bootstrap_windows.py frigate migrations testing-scripts
python -m compileall -q bootstrap_windows.py frigate migrations testing-scripts
python testing-scripts/check_windows_requirements.py
python testing-scripts/run_windows_unit_suite.py
```

For web changes:

```powershell
cd web
npm ci
npm run lint
npm run build:native
```

For documentation changes, run `npm ci` and `npm run build` from `docs`.
Regenerate derived API, schema, and translation artifacts with their repository
scripts when changing the corresponding source.

## Design rules

- Keep platform behavior in `frigate/util/platform.py` where practical. It must
  remain importable during early startup without importing other Frigate
  modules.
- Build paths with `pathlib` or `os.path`; do not introduce Unix root paths.
- Start child processes with Windows-safe argument lists and process flags.
- Keep the public HTTP listener authenticated and the trusted internal listener
  bound to loopback. Never trust client-supplied identity headers.
- Detector and acceleration additions must have a maintained native Windows
  runtime and CPython 3.12 x64 wheels where Python packages are required.
- User-visible strings belong in the localization system.
- Add regression tests for lifecycle, path, security, and configuration changes.

## Project layout

```text
bootstrap_windows.py   Windows installer and launcher generator
requirements.txt       authoritative CPython 3.12 Windows dependencies
frigate/               Python application
  api/                 FastAPI routes and native HTTP gateway
  config/              configuration models and validation
  detectors/           Windows-supported detector backends
  test/                unit and Windows integration tests
  util/                process, path, IPC, media, and service utilities
web/                   React/TypeScript application
docs/                  documentation site
migrations/            SQLite migrations
scripts/               PowerShell service tooling
testing-scripts/       validation and diagnostic scripts
winget-manifest/        package-manager manifest template
```

See [WINDOWS_COMPATIBILITY.md](WINDOWS_COMPATIBILITY.md) for the runtime
architecture and [the installation guide](docs/docs/frigate/installation.md)
for operator-facing behavior.
