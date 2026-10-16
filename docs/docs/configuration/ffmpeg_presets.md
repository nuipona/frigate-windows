---
id: ffmpeg_presets
title: FFmpeg Presets
---

# FFmpeg presets for Windows

Presets expand to tested FFmpeg arguments. Set them under `ffmpeg.hwaccel_args`; Frigate rejects presets and raw hardware arguments that require Linux, macOS, or embedded-device APIs.

## Decode presets

| Preset | Hardware path | Notes |
| --- | --- | --- |
| `preset-windows-d3d11va` | Direct3D 11 Video Acceleration | Broad Windows compatibility; decoded frames return to system memory |
| `preset-windows-dxva2` | DXVA2 | Compatibility option for older Windows drivers |
| `preset-windows-qsv` | Intel Quick Sync | Automatic codec selection |
| `preset-windows-qsv-h264` | Intel Quick Sync H.264 | Use for H.264 streams |
| `preset-windows-qsv-h265` | Intel Quick Sync HEVC | Use for H.265/HEVC streams |
| `preset-windows-nvenc` | NVIDIA CUDA/NVDEC | Requires an FFmpeg build and driver exposing CUDA decode |
| `preset-windows-amf` | AMD path using D3D11VA decode | Pairs with AMF encoding where used |
| `preset-windows-vulkan` | Vulkan video/filter path | Driver and codec support varies |

Example:

```yaml
ffmpeg:
  hwaccel_args: preset-windows-d3d11va
```

A camera may override the global value:

```yaml
cameras:
  driveway:
    ffmpeg:
      hwaccel_args: preset-windows-nvenc
      inputs:
        - path: rtsp://camera/stream
          roles: [detect, record]
```

Set `hwaccel_args: auto` to let Frigate inspect go2rtc and Windows display adapters. Explicit presets are preferable when a machine has several GPUs.

## Input presets

The network input presets remain available:

- `preset-rtsp-generic`
- `preset-rtsp-restream`
- `preset-rtsp-restream-low-latency`
- `preset-rtsp-udp`
- `preset-rtsp-blue-iris`
- `preset-rtmp-generic`
- `preset-http-jpeg-generic`
- `preset-http-mjpeg-generic`
- `preset-http-reolink`

Windows DirectShow inputs such as `video=Integrated Camera` may use explicit FFmpeg arguments. Linux `/dev/video*`, V4L2, VAAPI, DRM, RKMPP, and Unix-socket inputs are rejected.

## Recording output presets

Supported recording presets are:

- `preset-record-generic`
- `preset-record-generic-audio-aac`
- `preset-record-generic-audio-copy`
- `preset-record-mjpeg`
- `preset-record-jpeg`
- `preset-record-ubiquiti`

Custom arguments are accepted when they do not reference an unsupported platform API. Keep segmenting arguments intact so retention, playback, and exports can index recordings correctly.
