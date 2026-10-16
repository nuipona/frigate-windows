---
id: cpu
title: High CPU Usage
---

# High CPU usage on Windows

Open **System > Metrics** to identify the process, then confirm it in Task Manager or PowerShell:

```powershell
Get-Process python,ffmpeg,go2rtc -ErrorAction SilentlyContinue |
  Sort-Object CPU -Descending |
  Select-Object Id,ProcessName,CPU,WorkingSet,Path
```

## FFmpeg processes

High FFmpeg CPU usually means software decoding or transcoding.

1. Test the camera without hardware acceleration.
2. Select a supported Windows preset for the installed GPU.
3. Confirm the log shows the intended decoder and no fallback error.
4. Avoid unnecessary go2rtc video transcoding; browser-compatible H.264 can normally be copied.
5. Use a lower-resolution, lower-frame-rate detection stream.

D3D11VA is the vendor-neutral starting point. Intel QSV and NVIDIA CUDA/NVDEC can be more efficient on matching hardware. Hardware decode support depends on codec, bit depth, chroma format, driver, and GPU generation.

## Detection processes

CPU TFLite inference is expected to use CPU. Reduce detection stream resolution or FPS, track fewer labels, or configure a supported OpenVINO/ONNX accelerator. Increasing the number of CPU detector processes can increase throughput but also raises total CPU and memory use.

Motion detection gates object detection. Overly sensitive motion settings cause continuous inference; tune threshold, contour area, and masks rather than masking most of the scene.

## Enrichments

Semantic search, face recognition, license plate recognition, classification, and transcription can create bursts of work. Disable one feature at a time to isolate it. Select GPU only when the installed native provider supports that model; otherwise use CPU explicitly rather than relying on a failed accelerator fallback.

## Recording and exports

Normal recording copies encoded packets and should be inexpensive. Audio conversion, timestamp overlays, timelapses, mixed-codec exports, and incompatible stream transitions may encode video or audio. Export jobs are intentionally lower priority but still consume available CPU.

## Antivirus and indexing

Real-time scanning of frequently created recording segments can raise system CPU and disk latency. If organizational policy permits, evaluate narrowly scoped exclusions for the media and cache directories. Do not exclude the whole application or config directory without a security review.

## Power and thermal limits

Use an appropriate Windows power plan and inspect temperature/clock behavior with the hardware vendor's tools. Sustained throttling appears as rising inference time even when process CPU percentage does not change.

When asking for help, include the System metrics export, relevant FFmpeg log, camera codec/resolution/FPS, configured detector device, GPU model, and driver version. Remove credentials first.
