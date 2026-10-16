---
id: memory
title: High Memory Usage
---

# High memory usage on Windows

Frigate uses memory for decoded frames, object tracking, recording buffers, model runtimes, the database, and the web application. Windows named shared memory is backed by the page file and has no separate tunable shared-memory quota.

## Identify the process

Use **System > Metrics**, Task Manager, Resource Monitor, or:

```powershell
Get-Process python,ffmpeg,go2rtc -ErrorAction SilentlyContinue |
  Sort-Object WorkingSet64 -Descending |
  Select-Object Id,ProcessName,
    @{Name='WorkingSetMB';Expression={[math]::Round($_.WorkingSet64 / 1MB, 1)}},
    @{Name='PrivateMB';Expression={[math]::Round($_.PrivateMemorySize64 / 1MB, 1)}}
```

A stable working set is not a leak. Record values over several hours and compare private bytes after equivalent workloads.

## Common causes

- Too many high-resolution detection streams or excessive detection FPS.
- Large frame queues caused by a stalled consumer.
- Multiple detector processes loading separate copies of a model.
- Semantic-search, recognition, transcription, or custom-classification models enabled together.
- go2rtc/FFmpeg transcodes with large probe or queue buffers.
- A long export or reindex task.
- A camera repeatedly reconnecting and leaving work pending until timeout.

## Reduce use

1. Use a camera substream for detection.
2. Keep detection near 5 FPS unless the scene requires more.
3. Remove duplicate model devices unless throughput measurements justify them.
4. Disable enrichments one at a time to identify model memory.
5. Correct unstable camera streams and timestamp errors.
6. Keep the Windows page file system-managed on a volume with free space.

Do not disable the page file for a machine running GPU runtimes and multiple spawned Python processes.

## Shared-memory errors

A named shared-memory failure can result from address-space pressure, page-file exhaustion, stale processes, or security software. Stop the Frigate service and confirm all child Python and FFmpeg processes exit before restarting. A reboot clears orphaned Windows kernel objects if a force-terminated process left them behind.

## Collecting evidence

Capture:

- Frigate System metrics before and after growth;
- process private bytes and handles;
- enabled cameras, resolutions, and FPS;
- configured models and detector process counts;
- active enrichment/reindex/export work;
- the log around the first rapid increase.

Do not upload databases, memory dumps, or profiler output without reviewing them for credentials and image data.
