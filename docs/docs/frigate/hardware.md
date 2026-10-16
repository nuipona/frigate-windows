---
id: hardware
title: Hardware
---

# Windows hardware recommendations

Frigate is a continuously running video and inference workload. Camera count, resolution, frame rate, recording retention, object model, and enabled enrichments determine the required hardware.

## Supported platform

- 64-bit Microsoft Windows 10 or Windows 11, or Windows Server with the required media and GPU drivers;
- 64-bit CPython 3.12;
- local NTFS storage for the configuration and database;
- current network and graphics drivers.

Other operating systems and container runtimes are not supported by this repository.

## CPU and memory

A modern x86-64 CPU with at least four physical cores is a practical starting point. Allocate at least 8 GB RAM for a small installation and more for many cameras, large models, semantic search, transcription, or classification. CPU-only object detection works but scales less efficiently than supported GPU/NPU inference.

## Detection hardware

Native Windows supports:

- CPU TFLite;
- ONNX Runtime on CPU, DirectML, or an installed CUDA provider;
- OpenVINO on Intel CPU, GPU, or NPU;
- a compatible remote ZMQ detector.

See [Object Detectors](../configuration/object_detectors.md).

## Video hardware acceleration

Windows FFmpeg acceleration supports D3D11VA, DXVA2, Intel QSV, NVIDIA CUDA/NVENC, AMD AMF, and Vulkan where the installed driver and codec expose them. Start with D3D11VA and then select a vendor-specific preset if needed.

## Storage

Recordings are write intensive. Use a dedicated local SSD or HDD volume with adequate sustained write speed and retention capacity. Keep `config.yml` and the SQLite database on local NTFS; do not place the database on SMB or another network filesystem. Media directories may be moved with the documented `FRIGATE_*_DIR` environment variables.

Enable Windows long paths and avoid deeply nested base directories. Exclude the Frigate data directory from real-time antivirus scanning only if that is acceptable under the machine's security policy.

## Networking

Use wired Ethernet for the recorder and cameras whenever possible. The public web port defaults to 8971. Port 5000 is an unauthenticated internal API and is bound to loopback only. go2rtc uses its configured RTSP/WebRTC ports separately.
