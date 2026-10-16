---
id: updating
title: Updating
---

# Updating Frigate on Windows

Before updating, back up `%LOCALAPPDATA%\Frigate\config` and any media or database paths overridden through environment variables.

## Source checkout installation

From PowerShell in the Frigate checkout:

```powershell
git fetch origin
git checkout dev
git pull --ff-only
py -3.12 bootstrap_windows.py
```

The bootstrap reuses the virtual environment, installs the pinned dependency set, verifies the pinned FFmpeg, go2rtc, and model downloads, rebuilds the web interface when Node.js is installed, and rewrites the launcher.

If Frigate is installed as a Windows service, stop it before updating and start it afterward:

```powershell
Stop-Service Frigate
py -3.12 bootstrap_windows.py --install-service
Start-Service Frigate
```

Run those commands from an elevated PowerShell when changing the service registration.

## Release bundle

For a packaged release, stop Frigate, extract the new bundle over a new application directory, run its bootstrap or installer, and point the service at the new interpreter. Do not delete the data directory. Keep the old application directory until the new version has started and recordings play correctly.

## Configuration migrations

Frigate backs up and migrates older `config.yml` files at startup. Review release notes before skipping versions. A configuration using a detector, FFmpeg preset, path, or service option removed by the Windows-only release must be updated before startup.

Validate before restarting production:

```powershell
.\.venv\Scripts\python.exe -m frigate --validate-config
```

## Rollback

1. Stop Frigate.
2. Restore the previous application directory and virtual environment.
3. Restore the matching configuration/database backup if the newer version performed an incompatible migration.
4. Reinstall or repoint the Windows service.
5. Start Frigate and inspect the logs under `%LOCALAPPDATA%\Frigate\logs`.
