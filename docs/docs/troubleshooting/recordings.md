---
id: recordings
title: Recording Troubleshooting
---

# Recording troubleshooting on Windows

Recording needs a stable source, valid timestamps, a writable media directory, and compatible MP4 streams. Start with the camera's FFmpeg log and **System > Storage**.

## No new recording segments were created

Confirm recording is enabled and one input has the `record` role:

```yaml
record:
  enabled: true

cameras:
  front:
    ffmpeg:
      inputs:
        - path: rtsp://camera/main
          roles: [record]
```

A healthy camera regularly moves finalized segments into `recordings` beneath the Windows media directory. If no segment reaches the cache, test the exact source with bundled `ffprobe.exe` and inspect the first FFmpeg error rather than later restart messages.

## Segments exist but are not retained

Retention mode controls what survives cleanup:

- `all` keeps every segment for the configured period;
- `motion` keeps segments containing motion;
- `active_objects` keeps segments containing an active tracked object.

Broad motion masks, disabled detection, or an incorrect system clock can make expected footage ineligible. Storage-reserve cleanup can remove old segments earlier when the media volume is low.

## Incompatible audio codec

G.711 and some other camera audio codecs cannot be copied into MP4. Live view may still work while segment muxing fails. Transcode audio to AAC or remove it:

```yaml
ffmpeg:
  output_args:
    record: preset-record-generic-audio-aac
```

Use `preset-record-generic` when recordings should have no audio.

## Invalid or missing video stream in segment

Common causes include:

- a camera changing resolution, codec, profile, or parameter sets without a reconnect;
- corrupt packets or non-monotonic timestamps;
- a main/substream URL that sometimes returns a still image or error page;
- reconnects during segment finalization;
- unsupported codec data copied into MP4.

Update camera firmware, use a stable RTSP transport, and test the same URL for several minutes with FFmpeg. Avoid camera “smart codec” features that dynamically alter GOP/profile behavior.

## Segments are only one second long

The camera is usually disconnecting or resetting timestamps. Check packet loss, PoE power, Wi-Fi quality, connection limits, and keyframe interval. Frigate cannot join a continuous recording from a stream that repeatedly restarts.

## Recording cache maintenance error

The cache and media volume both need free space and Modify permission for the service account. Confirm their resolved paths in diagnostics. Antivirus or backup tools can delay renames; evaluate narrow exclusions only when permitted by policy.

Stop the service before manually clearing a stale cache. Do not delete files while FFmpeg or the recording maintainer is using them.

## Media directory is not writable

The default service media path is `%ProgramData%\Frigate\media`. For another volume:

```powershell
$path = 'E:\FrigateMedia'
icacls $path /grant 'NT AUTHORITY\LOCAL SERVICE:(OI)(CI)M' /T
```

Use the actual configured service account. Mapped drive letters are per-user and are normally unavailable to services. Prefer a local NTFS volume; if SMB is unavoidable, use a UNC path and an account with both share and NTFS permissions.

## Disk fills unexpectedly

Check:

- bitrate and number of record streams;
- continuous versus motion/object retention;
- alert and detection overrides;
- exports, which retention never deletes;
- debug snapshots and enrichment artifacts;
- the configured reserve and actual free space of the resolved media volume.

Approximate constant-bitrate use is `bitrate in Mbit/s × 10.8 GB/day` per camera.

## Database and files disagree

Do not move or delete recording files outside Frigate while it is running. Database repair cannot recreate missing video. If restoring a backup, restore database and media from a consistent point in time.

Keep `frigate.db` on local NTFS. Network filesystems and sync tools do not provide the locking semantics SQLite needs.

## No preview for the current hour

Preview generation depends on cached frames. A graceful UI/service restart can preserve eligible cache data; a forced termination, crash, or reboot can lose an incomplete interval. Completed recordings remain governed by normal retention.

## Export fails or freezes at a stream change

Exports need enough free cache/media space and read access to every source segment. Ranges crossing main/substream resolution or codec changes may require staging or software re-encoding. Inspect the export progress and FFmpeg error. Avoid changing camera codec settings inside the requested range.

## Diagnose a cached file

From PowerShell, run the bundled probe against the reported path:

```powershell
& 'C:\ProgramData\Frigate\ffmpeg\bin\ffprobe.exe' `
  -v error -show_streams -show_format 'C:\ProgramData\Frigate\cache\segment.mp4'
```

Adjust paths for the installation. Include probe output and the original camera FFmpeg error in a support request, with URLs and credentials redacted.
