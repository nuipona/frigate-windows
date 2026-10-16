---
id: hardware_acceleration_video
title: Video Decoding
---

# Hardware-accelerated video decoding on Windows

Frigate can decode camera streams through Windows GPU APIs. Hardware decoding reduces CPU use, but the selected FFmpeg build, Windows driver, codec, and GPU must all support the same path.

## Before enabling acceleration

1. Install the current graphics driver from Intel, NVIDIA, or AMD.
2. Install Frigate with `bootstrap_windows.py`; its pinned FFmpeg build includes the supported Windows backends.
3. Confirm software decoding works first.
4. Select one of the [`preset-windows-*` presets](./ffmpeg_presets.md).
5. Check the Frigate logs and GPU graphs on the System page.

## Recommended starting points

- **Any recent GPU:** `preset-windows-d3d11va`
- **Intel:** `preset-windows-qsv-h264` or `preset-windows-qsv-h265`
- **NVIDIA:** `preset-windows-nvenc`
- **Older systems:** `preset-windows-dxva2`
- **AMD:** start with `preset-windows-d3d11va`; AMF is used for supported encoding operations

```yaml
ffmpeg:
  hwaccel_args: preset-windows-d3d11va
```

Use a codec-specific Intel preset only when all streams inheriting it use that codec. Otherwise set the preset per camera.

## Selecting a GPU

`ffmpeg.gpu` is a zero-based adapter index where the selected FFmpeg backend supports adapter selection:

```yaml
ffmpeg:
  gpu: 1
  hwaccel_args: preset-windows-nvenc
```

Windows Task Manager, `Get-CimInstance Win32_VideoController`, and the Frigate diagnostics output can help identify installed adapters. Adapter ordering can differ between APIs, so confirm the loaded device in logs.

## Troubleshooting

If FFmpeg repeatedly exits:

- switch to `preset-windows-d3d11va`;
- verify whether the stream is H.264 or H.265;
- update the GPU driver;
- make sure Frigate is using the pinned `ffmpeg.exe`, not another executable on `PATH`;
- remove custom `-hwaccel`, filter, and pixel-format arguments while testing;
- use software decoding temporarily by setting `hwaccel_args: []`.

Linux VAAPI, DRM, V4L2, RKMPP, Jetson/NVMM, and Apple VideoToolbox presets are intentionally unsupported by the native Windows build.
