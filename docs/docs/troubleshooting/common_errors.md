---
id: common_errors
title: Common Errors
---

# Common Windows errors

Start with `%LOCALAPPDATA%\Frigate\logs\frigate.log` for an interactive install or `%ProgramData%\Frigate\logs\frigate.log` for the default service install. If another base directory was selected, use its `logs` folder.

## Frigate requires 64-bit Windows and CPython 3.12

The native runtime intentionally refuses unsupported operating systems, architectures, and Python versions. Verify:

```powershell
py -3.12 -c "import platform,struct; print(platform.platform()); print(struct.calcsize('P') * 8)"
```

Run `bootstrap_windows.py` with that interpreter. A Microsoft Store alias or 32-bit Python is not supported.

## FFmpeg or go2rtc executable not found

Rerun the bootstrap from the repository root:

```powershell
py -3.12 bootstrap_windows.py --skip-web
```

Do not rename downloaded files. Antivirus quarantine, an incomplete archive, or changing `FRIGATE_BASE_DIR` after installation are common causes. Frigate checks executable names and rejects a directory where a file is required.

## Port 8971 is already in use

Find the owner from an elevated PowerShell session:

```powershell
Get-NetTCPConnection -LocalPort 8971 -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

Stop the conflicting process or change `networking.listen.external`. The external and internal ports must differ. Port `5000` remains loopback-only.

## The service starts and immediately stops

Run these commands from an elevated shell:

```powershell
Get-Service Frigate
Get-WinEvent -LogName Application -MaxEvents 50 |
  Where-Object ProviderName -Match 'Frigate|Python'
Get-Content "$env:ProgramData\Frigate\logs\frigate.log" -Tail 200
```

Typical causes are an invalid config, a missing service environment variable, a data-directory ACL that excludes the service account, or a partial TLS certificate pair. Validate using the service installation's interpreter and environment.

## Permission denied or database cannot be opened

The service account needs Modify permission on config, media, cache, temporary, and log directories. Keep `frigate.db` on local NTFS. Mapped drive letters from an interactive user are not available to a service; use a UNC path with explicit account permissions only for non-database storage.

## Database is locked

Do not run two Frigate instances against one database. Stop the service before launching an interactive copy. Antivirus, backup, and indexing tools can hold SQLite sidecar files; configure exclusions only if permitted by your security policy. Never put the database on SMB or another network filesystem.

## TLS certificate or private key error

`tls.cert_file` and `tls.key_file` must either both exist or both be absent. When both are absent, Frigate generates a self-signed pair. Custom files must be unencrypted PEM and readable by the service account.

If Frigate starts but a client rejects HTTPS, install a certificate with the hostname in its Subject Alternative Name and a chain trusted by that client.

## Camera has no frames

Test the exact stream from the Frigate host:

```powershell
& "$env:LOCALAPPDATA\Frigate\ffmpeg\bin\ffplay.exe" `
  'rtsp://user:password@camera/stream'
```

Then check DNS, Windows Firewall, credentials, camera connection limits, and codec support. Start without hardware acceleration. Add a Windows acceleration preset only after software decoding works.

## Hardware acceleration initialization failed

Update the display driver, confirm the GPU appears in Device Manager, and choose a preset reported by **System > Hardware**. Remote Desktop and virtual machines may expose a different adapter from a local session. See [GPU troubleshooting](./gpu.md).

## Detector is not running

The configured device must be present in the hardware probe. Native Windows supports CPU TFLite, OpenVINO, ONNX Runtime, and external ZMQ detectors. ONNX models need a compatible execution provider and explicit model path. Review [Object Detectors](/configuration/object_detectors).

## Configuration contains an unsupported preset or path

Validation rejects Unix device paths, sockets, unsupported detector names, and non-Windows FFmpeg backends. Replace copied configuration rather than attempting to pass a Linux device or argument through unchanged.

```powershell
.\.venv\Scripts\python.exe -m frigate --validate-config
```

## Long path or reserved-name error

Keep `FRIGATE_BASE_DIR` short and enable Windows long-path support. Camera names are case-insensitive and cannot use reserved device names such as `CON`, `PRN`, `AUX`, `NUL`, `COM1`, or `LPT1`.

## Recording errors

See [Recording troubleshooting](./recordings.md) for timestamp, codec, disk, retention, and segment problems. Do not delete database rows or recording files while Frigate is running.
