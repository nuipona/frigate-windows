---
id: system
title: System
---

# Advanced Windows system configuration

## Data directories

Frigate defaults to `%LOCALAPPDATA%\Frigate` for an interactive install. The bootstrap creates:

- `config` — configuration, secrets, models, and database;
- `media` — recordings, clips, previews, and exports;
- `cache` — transient media and downloaded installer artifacts;
- `tmp` — process markers and temporary files;
- `logs` — native application and service logs.

Override paths before startup with:

```powershell
$env:FRIGATE_BASE_DIR = 'D:\Frigate'
$env:FRIGATE_CONFIG_DIR = 'D:\Frigate\config'
$env:FRIGATE_MEDIA_DIR = 'E:\FrigateMedia'
$env:FRIGATE_CACHE_DIR = 'D:\Frigate\cache'
$env:FRIGATE_TMP_DIR = 'D:\Frigate\tmp'
```

The Windows service receives its own environment. Use the service installer to persist service paths rather than relying on variables in an interactive PowerShell profile.

Keep the SQLite database on local NTFS. Media can live on another local volume. Network shares must be available to the service account at boot and are not recommended for the database.

## Environment variables and secrets

`environment_vars` in `config.yml` supplies non-secret process settings. A `secrets.yaml` next to `config.yml` supplies `FRIGATE_*` substitutions without placing values in the main configuration.

Precedence is:

1. process environment;
2. `secrets.yaml`;
3. `environment_vars`.

Service installations should set secrets in the service environment or protect `secrets.yaml` with an ACL granting access only to Administrators and the service account.

## Ports

```yaml
networking:
  listen:
    external: 8971
    internal: 5000
```

Both values are integer TCP ports and must differ. The public gateway listens on Windows network interfaces. The unauthenticated internal gateway is always loopback-only.

Use Windows Firewall to restrict inbound access:

```powershell
New-NetFirewallRule -DisplayName 'Frigate HTTPS' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8971 `
  -RemoteAddress LocalSubnet
```

Adapt the profile and remote addresses to the deployment. Do not add an inbound rule for port 5000.

## FFmpeg and go2rtc paths

The bootstrap installs `ffmpeg.exe` and `ffprobe.exe` under the config directory and `go2rtc.exe` in the native data layout. Frigate also accepts explicit executable paths. Paths containing spaces are supported.

If executable discovery fails, rerun:

```powershell
py -3.12 bootstrap_windows.py --skip-web
```

Downloaded archives are pinned by SHA-256. Do not replace them with an unverified binary under the same filename.

## Process model

All multiprocessing uses the Windows `spawn` method. Frigate assigns Windows process priority classes, places descendants in a Job Object, and performs graceful service shutdown through a named event. The PowerShell launcher uses a restart marker so an intentional UI restart is distinguishable from a normal stop.

## Long paths

Enable long path support in Windows and keep the base directory reasonably short. Frigate warns when camera and media paths approach legacy `MAX_PATH` limits.

## Configuration validation

Run validation from the installed environment:

```powershell
.\.venv\Scripts\python.exe -m frigate --validate-config
```

A nonzero exit code indicates invalid configuration. Validation enforces Windows detector names, FFmpeg presets, reserved filenames, case-insensitive camera-name uniqueness, port separation, and proxy security requirements.
