<p align="center">
  <img align="center" alt="logo" src="docs/static/img/branding/frigate.png">
</p>

# Gannet NVR™ - Realtime Object Detection for IP Cameras

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

\[English\] | [简体中文](README_CN.md)

A complete and local NVR designed for [Home Assistant](https://www.home-assistant.io) with AI object detection, running realtime detection locally for IP cameras.

Use of a GPU or AI accelerator is highly recommended: accelerators outperform even the best CPUs with very little overhead. See the supported [object detectors](https://docs.frigate.video/configuration/object_detectors/).

- Tight integration with Home Assistant via a [custom component](https://github.com/blakeblackshear/frigate-hass-integration)
- Only looks for objects when and where it is necessary, to minimize resource use
- Multiprocessing design that favors realtime over processing every frame
- Low overhead motion detection decides where object detection runs
- Detection runs in separate processes for maximum FPS
- MQTT for integration into other systems
- Recording with retention based on detected objects, plus 24/7 recording
- Re-streaming via RTSP to reduce the number of connections to your camera
- WebRTC and MSE for low-latency live view

## This fork: native Windows

`nuipona/frigate-windows` tracks `blakeblackshear/frigate` and adds a native Windows install: Frigate runs as a normal Windows process, with no Docker and no WSL. Frigate itself serves the API, the media endpoints and the web interface, so there is no nginx, no s6 and no `/dev/shm`.

Install from an ordinary PowerShell window:

```powershell
git clone https://github.com/nuipona/frigate-windows.git
cd frigate-windows
py -3.12 bootstrap_windows.py
powershell -ExecutionPolicy Bypass -File .\run_frigate.ps1
```

The bootstrap script needs 64-bit CPython 3.12. It creates the venv, installs the Windows requirements, fetches pinned FFmpeg and go2rtc builds, builds the web interface when Node.js is available, and writes a launcher that restarts Frigate when Frigate asks to be restarted. Node.js is only needed for that frontend build: the `windows_bundle` release job publishes a zip with `web/dist` already built.

Windows-specific behaviour:

- Detectors: CPU (TFLite, ONNX Runtime, OpenVINO) and Intel GPU/NPU through OpenVINO, plus AMD, Intel and Nvidia GPU inference through DirectML (`onnxruntime-directml`, installed by default). Coral, Hailo and other accelerators that need Linux-only drivers are unavailable, and a config that names one fails validation with the list Windows can run.
- Video: `preset-windows-d3d11va`, `preset-windows-dxva2`, `preset-windows-qsv` and `preset-windows-nvenc`, and `ffmpeg.hwaccel_args: auto` detects which one to use.
- Files: everything lives under one base directory, `%LOCALAPPDATA%\Frigate` by default, relocatable with `FRIGATE_BASE_DIR`.
- Optional SCM service: `bootstrap_windows.py --install-service`.

Read the [Windows installation guide](docs/docs/frigate/installation.md#windows) for setup, ports and tuning, and [WINDOWS_COMPATIBILITY.md](WINDOWS_COMPATIBILITY.md) for the port audit: what was changed, what is verified, and what is still open.

## Documentation

View the documentation at https://docs.frigate.video. To build this fork's docs locally, see [docs/README.md](docs/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## Donations

If you would like to make a donation to support development, please use [Github Sponsors](https://github.com/sponsors/blakeblackshear).

## License

This project is licensed under the **MIT License**.

- **Code:** The source code, configuration files, and documentation in this repository are available under the [MIT License](LICENSE). You are free to use, modify, and distribute the code as long as you include the original copyright notice.
- **Trademarks:** The "Frigate" name, the "Frigate NVR" brand, and the Frigate logo are **trademarks of Frigate, Inc.** and are **not** covered by the MIT License.

Please see our [Trademark Policy](TRADEMARK.md) for details on acceptable use of our brand assets.

## Screenshots

### Live dashboard

<div>
<img width="800" alt="Live dashboard" src="https://github.com/blakeblackshear/frigate/assets/569905/5e713cb9-9db5-41dc-947a-6937c3bc376e">
</div>

### Streamlined review workflow

<div>
<img width="800" alt="Streamlined review workflow" src="https://github.com/blakeblackshear/frigate/assets/569905/6fed96e8-3b18-40e5-9ddc-31e6f3c9f2ff">
</div>

### Multi-camera scrubbing

<div>
<img width="800" alt="Multi-camera scrubbing" src="https://github.com/blakeblackshear/frigate/assets/569905/d6788a15-0eeb-4427-a8d4-80b93cae3d74">
</div>

### Built-in mask and zone editor

<div>
<img width="800" alt="Built-in mask and zone editor" src="https://github.com/blakeblackshear/frigate/assets/569905/d7885fc3-bfe6-452f-b7d0-d957cb3e31f5">
</div>

## Translations

We use [Weblate](https://hosted.weblate.org/projects/frigate-nvr/) to support language translations. Contributions are always welcome.

<a href="https://hosted.weblate.org/engage/frigate-nvr/">
<img src="https://hosted.weblate.org/widget/frigate-nvr/multi-auto.svg" alt="Translation status" />
</a>

---

**Copyright © 2026 Frigate, Inc.**
