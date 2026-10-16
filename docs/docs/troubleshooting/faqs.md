---
id: faqs
title: Frequently Asked Questions
---

# Frequently asked questions

## Where are Frigate's files?

Interactive installs default to `%LOCALAPPDATA%\Frigate`. The Windows service installer defaults to `%ProgramData%\Frigate`. `FRIGATE_BASE_DIR` can select another root before installation. Subdirectories contain config, media, cache, temporary files, logs, models, and tools.

## Where are the logs?

Use **System > Logs** or read `logs\frigate.log` and `logs\go2rtc.log` below the data root:

```powershell
Get-Content "$env:ProgramData\Frigate\logs\frigate.log" -Wait
```

## Why does `localhost` not reach MQTT or a camera?

`localhost` means the Windows machine running Frigate. Use the broker/camera's LAN address or DNS name when it runs elsewhere. For a local broker, confirm the service is listening on loopback and that the Frigate service account can connect.

## Can I expose port 5000?

No. Port `5000` is an unauthenticated loopback listener for Frigate's own media jobs. Users and integrations connect to the authenticated public listener on port `8971`.

## Why does HTTPS show a warning?

Frigate generates a self-signed certificate when no pair exists. Install a trusted certificate with the correct DNS/IP Subject Alternative Names, or trust a private CA on each client. Do not disable TLS on an untrusted network.

## How do I change or recover the administrator password?

Use the user-management UI while signed in. If access is lost, set `auth.reset_admin_password: true`, restart once, retrieve the generated password from the protected log, then set the option back to `false`.

## What does the config `version` field do?

It records the last configuration format migration. Frigate backs up and rewrites older YAML during migration, then updates the field. Do not manually increase it. Back up `config.yml`, `secrets.yaml`, and `frigate.db` before upgrades.

## Why is the service using different files from an interactive run?

Services do not inherit the signed-in user's `%LOCALAPPDATA%` or PowerShell environment. The installer pins `FRIGATE_BASE_DIR` and supported startup variables in the service registry. Use `install_frigate_service.ps1 -Action Status` to inspect it and `-Action Configure` after changing startup settings.

## Can media be stored on a network share?

Local NTFS is recommended. Never place `frigate.db` on a network share. If media must use SMB, run the service under an account with explicit share and NTFS permissions, use a UNC path rather than a mapped drive, and make the share available at boot. Network interruptions can create recording gaps.

## Why does CPU usage remain high?

Confirm video hardware decoding actually initialized, use a lower-resolution detection stream around 5 FPS, and check detector/enrichment inference. See [High CPU Usage](./cpu.md).

## Why is my GPU not shown?

Update its Windows driver and check **System > Hardware**. Video decode, OpenVINO, DirectML, CUDA, and NVML are separate APIs; success in one does not prove another is available. See [GPU Troubleshooting](./gpu.md).

## Why are recordings missing?

Recording must be enabled and an input must have the `record` role. Retention mode, motion, storage reserve, timestamps, unsupported audio, permissions, or a full volume can remove or prevent segments. See [Recording Troubleshooting](./recordings.md).

## Audio in recordings

The record stream must contain audio and the output arguments must either copy a codec valid in MP4 or transcode it to AAC. G.711 (`pcm_alaw`/`pcm_mulaw`) cannot be copied into MP4. Use the generic AAC recording preset when needed:

```yaml
ffmpeg:
  output_args:
    record: preset-record-generic-audio-aac
```

Casting clients may require an audio track even when the camera has no useful sound.

## How do I reduce camera connections?

Configure one camera source in go2rtc and use its local RTSP restream for detection, recording, and other clients. Keep the go2rtc management API on loopback.

## Can camera processing be turned off temporarily?

Yes. Use the camera controls in the UI, Home Assistant, or MQTT. Runtime state persists across Frigate restarts. Set the camera's configured state to Disabled when it should remain administratively unavailable.

## How do I request help safely?

Include the Frigate version, Windows build, relevant log excerpt, camera codec/resolution/FPS, selected detector and acceleration preset, and hardware/driver versions. Remove camera credentials, signed URLs, JWTs, API keys, proxy secrets, and private images.
