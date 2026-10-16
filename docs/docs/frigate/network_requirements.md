---
id: network_requirements
title: Network Requirements
---

# Network requirements

Frigate can process cameras entirely on the local Windows network after required binaries and models have been installed. Optional cloud services require outbound access.

## Installation

`bootstrap_windows.py` needs HTTPS access to:

- Python package indexes for pinned dependencies;
- the Node package registry for the web build;
- GitHub release assets for the pinned Windows FFmpeg and go2rtc archives.
- TensorFlow Hub for the digest-pinned YAMNet audio model.

The bootstrap verifies downloaded application archives with fixed SHA-256 digests. A proxy can be supplied through the normal Windows/Python/npm environment settings.

## Camera network

The Windows host needs outbound access to every camera stream and ONVIF endpoint. Typical ports include RTSP `554`, HTTP/HTTPS, and vendor-specific ONVIF ports. Use fixed camera addresses or reliable DNS.

Frigate normally opens no inbound camera-facing connection. PTZ, talkback, and restreaming may require extra camera permissions.

## User and integration access

| Purpose | Default | Direction |
| --- | --- | --- |
| Authenticated UI and API | TCP `8971` | inbound to Windows host |
| Internal API | TCP `5000`, loopback only | local process only |
| go2rtc RTSP restream | TCP `8554` | optional inbound |
| WebRTC | TCP/UDP `8555` by default | optional inbound |
| MQTT | broker-defined, commonly TCP `1883` or `8883` | outbound |

Restrict Windows Firewall rules to the required subnets or integration hosts. Never create an inbound rule for port `5000`.

## Runtime model downloads

Models are cached below the configured data root, normally:

```text
%LOCALAPPDATA%\Frigate\config\model_cache
```

A Windows service normally uses `%ProgramData%\Frigate` unless another base directory was selected. Features that may download model data on first use include:

- default CPU object detection;
- semantic search;
- face recognition;
- license plate recognition;
- bird classification;
- audio transcription;
- custom-classification training.

Sources include GitHub, Hugging Face, Google-hosted model data, and provider-specific model repositories. Enable and initialize every required feature once while online before enforcing an outbound block.

Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in the Windows service environment only after all Hugging Face artifacts are cached. Missing files then cause startup or feature errors instead of downloads.

## Optional internet services

- **Frigate+** communicates with the Frigate+ API to authenticate, list/download models, and upload explicitly submitted images.
- **Generative AI** communicates with each configured provider's endpoint. A local Ollama or llama.cpp provider needs only LAN access.
- **Push notifications** use browser-vendor push endpoints.
- **WebRTC STUN** uses the configured STUN service for candidate discovery.
- **Update checks** query release metadata when enabled by the UI.

Disable the corresponding feature to eliminate its traffic. Frigate does not require inbound internet exposure for any of them.

## Offline preparation

1. Run the Windows bootstrap while connected.
2. Start Frigate with every required local enrichment and detection model enabled.
3. Confirm the model cache is populated and restart successfully without downloads.
4. Back up the config, database, model cache, and pinned installer artifacts.
5. Apply outbound firewall rules and test camera, MQTT, UI, recording, live view, and enrichment behavior.

Do not copy a model cache between architectures or incompatible package versions without testing it. Keep application updates and their matching model/runtime updates together.
