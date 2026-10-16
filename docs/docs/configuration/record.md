---
id: record
header_title: Recording
---

# Recording

Frigate records directly from the stream assigned the `record` role. A separate lower-resolution stream can be assigned to `detect`.

```yaml
record:
  enabled: true
  retain:
    days: 3
    mode: motion
  alerts:
    retain:
      days: 30
  detections:
    retain:
      days: 14

cameras:
  front:
    ffmpeg:
      inputs:
        - path: rtsp://user:password@camera/low
          roles: [detect]
        - path: rtsp://user:password@camera/high
          roles: [record]
```

Recording settings can be defined globally and overridden per camera.

## Retention modes

- `all` retains all segments for the configured number of days.
- `motion` retains segments containing motion.
- `active_objects` retains segments containing an active tracked object.

Alert and detection retention can preserve event recordings longer than the continuous-recording period. Frigate deletes media according to the first applicable retention rule and available-storage safeguards.

## Storage location

Media defaults to `%LOCALAPPDATA%\Frigate\media`. Set `FRIGATE_MEDIA_DIR` before installation to use another local volume. For a service installation, configure the path through the service installer so it is present at boot.

Use NTFS and grant Modify permission to the Frigate service account. Avoid removable media and sleep-prone network shares. Do not store the SQLite database on a network share.

Example ACL command from an elevated PowerShell session:

```powershell
$path = 'E:\FrigateMedia'
icacls $path /grant 'NT AUTHORITY\LOCAL SERVICE:(OI)(CI)M' /T
```

Replace the account with the one selected during service installation.

## Storage planning

Storage depends on camera bitrate, retention period, and retained-event activity. For a constant stream:

```text
GB per day ≈ bitrate in Mbit/s × 10.8
```

A 4 Mbit/s stream therefore uses roughly 43 GB per day before filesystem overhead. Variable-bitrate cameras and motion-only retention can differ substantially.

Frigate monitors free space on the media volume and may remove old recordings to keep the configured reserve. Windows volume metrics are reported in diagnostics.

## Exporting

Exports are written beneath the media directory and are available only to authorized users with access to the camera. Ensure enough free space for both source segments and the completed export.

## Troubleshooting

- Verify the record input contains stable timestamps and a supported codec.
- Ensure the service account can create, rename, and delete files in the media directory.
- Exclude the media directory from real-time antivirus scanning only if allowed by your security policy; exclusions reduce protection.
- Check drive health and free space before increasing retention.
- Review FFmpeg logs when recordings contain gaps or cannot be finalized.
