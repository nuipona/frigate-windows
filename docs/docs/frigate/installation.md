---
id: installation
title: Windows installation
---

Frigate is installed directly on 64-bit Windows. Docker, WSL, Linux packages,
and Home Assistant add-on images are not supported by this edition.

## Requirements

- Windows 10 or Windows 11, 64-bit
- 64-bit CPython 3.12 (`py -3.12` must work)
- Git
- Node.js 22 or newer when building the web interface from source
- An optional MQTT broker for MQTT integrations

The installer fetches verified native builds of FFmpeg and go2rtc plus the CPU detection and YAMNet audio models. You do not
need to install either tool separately.

## Windows

Open PowerShell in the repository checkout and run:

```powershell
py -3.12 bootstrap_windows.py
```

The bootstrap performs these steps:

1. verifies Windows, CPython 3.12, and a 64-bit interpreter;
2. creates `.venv`;
3. installs the pinned dependencies from `requirements.txt`;
4. downloads and verifies FFmpeg 8.1.2, go2rtc 1.9.14, and the CPU detection and YAMNet audio models;
5. builds the React web application when Node.js is available;
6. writes `run_frigate.ps1`; and
7. creates a Start Menu shortcut unless `--skip-shortcut` is used.

Start Frigate with:

```powershell
.\run_frigate.ps1
```

Open `http://localhost:8971`. On first start, the generated administrator
password is written to the Frigate log.

### Bootstrap options

```text
--venv PATH              choose the virtual-environment directory
--directml               use ONNX Runtime DirectML for AMD, Intel, or NVIDIA GPU inference
--skip-venv              reuse an existing virtual environment
--skip-deps              do not install Python requirements
--skip-binaries          do not fetch FFmpeg, go2rtc, or the detection and audio models
--skip-web               do not build the web application
--skip-shortcut          do not create a Start Menu shortcut
--install-service        register the Windows service
--add-defender-exclusion add the Frigate data directory to Microsoft Defender exclusions
```

Use `--add-defender-exclusion` only after reviewing the command and deciding
that the performance trade-off is appropriate for the host.

## Data locations

The default writable root is `%LOCALAPPDATA%\Frigate` for an interactive user.
It contains:

```text
Frigate\
  cache\               model downloads and temporary data
  config\
    config.yml          main configuration
    frigate.db          SQLite database
    ffmpeg\             installed ffmpeg.exe and ffprobe.exe
    go2rtc.exe          installed live-stream helper
    model_cache\        object-detection models
  logs\                 Frigate and go2rtc logs
  media\
    clips\              snapshots and thumbnails
    exports\            user exports
    recordings\         recording segments
  tmp\                  runtime temporary files and restart marker
```

Relocate the whole tree before running the bootstrap:

```powershell
$env:FRIGATE_BASE_DIR = 'D:\Frigate'
py -3.12 bootstrap_windows.py
```

Or override individual directories with `FRIGATE_CONFIG_DIR`,
`FRIGATE_MEDIA_DIR`, `FRIGATE_CACHE_DIR`, and `FRIGATE_TMP_DIR`. Persist these
variables in the account or service environment; setting them in one temporary
PowerShell window does not affect later launches.

## Configuration

If the bootstrap did not create a configuration, create
`%LOCALAPPDATA%\Frigate\config\config.yml`. A minimal configuration is:

```yaml
mqtt:
  enabled: false

models:
  - devices:
      - cpu

cameras:
  front_door:
    ffmpeg:
      inputs:
        - path: rtsp://user:password@camera/stream
          roles:
            - detect
            - record
    detect:
      width: 1280
      height: 720
```

Camera names may not use Windows reserved device names such as `CON`, `NUL`, or
`COM1`, and two names may not differ only by letter case.

## Ports and firewall

| Port | Binding | Purpose |
| --- | --- | --- |
| `8971/tcp` | `0.0.0.0` by default | Authenticated web interface and API |
| `5000/tcp` | `127.0.0.1` only | Trusted internal media jobs |
| `1984/tcp` | loopback child service | go2rtc API used by Frigate |
| `8554/tcp` | go2rtc configuration | Optional RTSP restreaming |
| `8555/tcp` and UDP | go2rtc configuration | Optional WebRTC media |

The public and internal ports must be different. Change the public binding with
`FRIGATE_WEB_HOST` and `FRIGATE_WEB_PORT`. For a local-only install:

```powershell
$env:FRIGATE_WEB_HOST = '127.0.0.1'
```

Allow port 8971 through Windows Defender Firewall only when other computers need
to reach Frigate. Keep port 5000 blocked from the network.

## Hardware acceleration

Set one of the Windows decode presets under `ffmpeg.hwaccel_args`:

- `preset-windows-d3d11va`
- `preset-windows-dxva2`
- `preset-windows-qsv-h264`
- `preset-windows-qsv-h265`

Encoding supports native NVIDIA NVENC, Intel QSV, and AMD AMF presets. `auto`
uses Windows adapter and go2rtc diagnostics to choose a decoder and falls back
to software when no compatible device is found.

Object detection supports CPU TFLite, ONNX Runtime, OpenVINO, and ZMQ. Use
`bootstrap_windows.py --directml` to install the DirectML ONNX provider for GPU
inference. Accelerators that require Unix device nodes or Linux vendor SDKs are
not available.

## Install as a Windows service

Run an elevated PowerShell session:

```powershell
py -3.12 bootstrap_windows.py --install-service
```

The service wrapper uses pywin32 and starts Frigate from the repository/install
root. Service data may use a different profile from an interactive launch; set
`FRIGATE_BASE_DIR` as a machine or service environment variable when you need a
fixed location.

Useful commands:

```powershell
Get-Service Frigate
Start-Service Frigate
Stop-Service Frigate
Restart-Service Frigate
```

The API restart action creates a restart marker and exits cleanly; the launcher
or service wrapper then starts a new process.

## Updating

1. Stop Frigate or the Windows service.
2. Back up the configuration directory and media/database data.
3. Update the checkout.
4. Run `py -3.12 bootstrap_windows.py` again to refresh dependencies, binaries,
   and the web build.
5. Start Frigate and review the logs for configuration migrations.

Do not downgrade a database without restoring the matching backup.

## Uninstall

Stop and remove the service first if it was installed, then remove the checkout,
virtual environment, Start Menu shortcut, and Frigate data directory. Preserve
`config.yml`, `frigate.db`, and media first if they are needed later.

Removing the application does not remove Python, Node.js, Git, firewall rules,
or a Defender exclusion that you created. Remove those separately if desired.

## Troubleshooting

- **The bootstrap rejects Python:** install 64-bit CPython 3.12 and invoke it
  explicitly with `py -3.12`.
- **The UI does not load:** install Node.js and rerun the bootstrap without
  `--skip-web`.
- **Live view is unavailable:** rerun without `--skip-binaries`, then inspect
  the go2rtc log in the native log directory.
- **A camera URL works elsewhere but not in Frigate:** run the installed
  `ffprobe.exe` against the URL from the same Windows account.
- **Files intermittently fail to move or delete:** antivirus and indexing tools
  can hold media handles. Frigate retries sharing violations; persistent cases
  should be handled with narrowly scoped exclusions.
- **Long-path warning:** enable Win32 long paths through Windows policy or move
  `FRIGATE_BASE_DIR` closer to a drive root.
- **No GPU acceleration:** update the Windows display driver and verify the
  selected FFmpeg preset or ONNX provider in the System page.
