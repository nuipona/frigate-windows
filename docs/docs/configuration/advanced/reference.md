---
id: reference
title: Native Windows Reference
---

# Native Windows configuration reference

The schema served by the running application is the authoritative reference for every field and default:

```text
https://frigate-host:8971/api/config/schema.json
```

The Settings UI renders that same schema. Add only values that differ from defaults; copying a complete generated configuration makes upgrades harder.

## Minimal configuration

```yaml
mqtt:
  enabled: false

cameras:
  front:
    enabled: false
    ffmpeg:
      inputs:
        - path: rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera/stream
          roles: [detect]
```

## Native paths

Interactive installs default to `%LOCALAPPDATA%\Frigate`; services default to `%ProgramData%\Frigate`. Set `FRIGATE_BASE_DIR` before startup to change the root. Config, media, cache, temporary files, logs, model cache, and installed tools each have dedicated subdirectories.

Omit path fields to use that layout. When an explicit path is needed, quote it:

```yaml
database:
  path: 'D:\Frigate\config\frigate.db'
```

Keep SQLite on local NTFS. Camera names and paths follow Windows case-insensitive and reserved-name rules.

## Secrets and environment

```yaml
environment_vars:
  HF_HUB_OFFLINE: "1"

mqtt:
  host: "{FRIGATE_MQTT_HOST}"
  user: "{FRIGATE_MQTT_USER}"
  password: "{FRIGATE_MQTT_PASSWORD}"
```

`FRIGATE_*` substitutions come from the process environment, `secrets.yaml`, then `environment_vars`. Security-sensitive startup controls such as `PLUS_API_KEY` and `GO2RTC_ALLOW_ARBITRARY_EXEC` must be in the process or Windows service environment.

## Web, TLS, and authentication

```yaml
networking:
  ipv6:
    enabled: false
  listen:
    internal: 5000
    external: 8971

tls:
  enabled: true
  cert_file: 'C:\ProgramData\Frigate\config\tls\fullchain.pem'
  key_file: 'C:\ProgramData\Frigate\config\tls\privkey.pem'

auth:
  enabled: true
  cookie_name: frigate_token
  cookie_secure: true
  session_length: 86400
  refresh_time: 1800
  failed_login_rate_limit: null
  trusted_proxies: []
  hash_iterations: 600000
  roles:
    driveway_viewers:
      - driveway
```

The internal listener is always loopback-only and cannot share the public port. When both TLS files are absent Frigate creates a self-signed pair. Custom role camera lists cannot be empty.

Proxy-supplied identity requires native authentication to be disabled and a shared secret:

```yaml
auth:
  enabled: false

proxy:
  header_map:
    user: X-Remote-User
    role: X-Remote-Groups
    role_map:
      admin: [frigate-admins]
  default_role: none
  separator: ","
  auth_secret: "{FRIGATE_PROXY_AUTH_SECRET}"
```

## Detection models

With no `models` section, Frigate runs its downloaded TFLite model on CPU. Supported detector names are `cpu`, `openvino`, `onnx`, and `zmq`.

```yaml
models:
  - scene: default
    devices:
      - openvino:AUTO
    path: 'D:\Frigate\models\detector.xml'
    labelmap_path: 'D:\Frigate\models\labels.txt'
    width: 320
    height: 320
    input_pixel_format: bgr
    input_tensor: nchw
    input_dtype: float
    model_type: yolo-generic
```

Each model needs at least one device, all devices for a model must use the same detector, and scene names must be unique. ONNX models require an explicit path. Frigate+ uses `path: plus://<model-id>` and fills model metadata automatically.

## FFmpeg and cameras

```yaml
ffmpeg:
  hwaccel_args: preset-windows-d3d11va

cameras:
  front:
    enabled: true
    ffmpeg:
      inputs:
        - path: rtsp://camera/substream
          roles: [detect]
        - path: rtsp://camera/mainstream
          roles: [record]
    detect:
      enabled: true
      width: 1280
      height: 720
      fps: 5
    objects:
      track: [person, car]
    motion:
      enabled: true
```

Supported acceleration presets are documented in [FFmpeg Presets](../ffmpeg_presets.md). Unix device paths, Unix sockets, and non-Windows accelerator arguments are rejected.

Roles include `detect`, `record`, `record_sub`, and `audio`. Recording, sub-recording, and audio detection each require the corresponding input role.

## go2rtc and live view

```yaml
go2rtc:
  streams:
    front:
      - rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera/mainstream
  webrtc:
    candidates:
      - 192.168.1.20:8555
      - stun:8555

live:
  streams:
    front: front
```

The generated management API binds to `127.0.0.1:1984` by default and is available through the authenticated Frigate gateway. HomeKit pairing state is stored separately in `go2rtc_homekit.yml`.

## Recording and snapshots

```yaml
record:
  enabled: true
  retain:
    days: 7
    mode: motion
  alerts:
    retain:
      days: 30
      mode: motion
  detections:
    retain:
      days: 14
      mode: motion

snapshots:
  enabled: true
  timestamp: false
  bounding_box: true
  crop: false
  retain:
    default: 10
```

Retention modes are `all`, `motion`, and `active_objects`. Exports are not deleted by recording retention.

## Review, objects, masks, and zones

```yaml
objects:
  track: [person, car]
  filters:
    person:
      min_score: 0.5
      threshold: 0.7

review:
  alerts:
    labels: [person, car]
    required_zones: [property]
  detections:
    labels: [person, car]

cameras:
  front:
    zones:
      property:
        coordinates: "0.05,0.95,0.95,0.95,0.95,0.20,0.05,0.20"
        objects: [person, car]
```

Coordinates may be drawn in the UI. Avoid broad motion masks; they prevent object detection from being triggered in masked regions.

## Enrichments

```yaml
semantic_search:
  enabled: false
  device: CPU

face_recognition:
  enabled: false
  device: CPU

lpr:
  enabled: false
  device: CPU

audio_transcription:
  enabled: false
  device: CPU
```

Use `GPU` or another device only when the System hardware probe and feature documentation show a compatible native Windows provider.

## MQTT, notifications, and telemetry

```yaml
mqtt:
  enabled: true
  host: mqtt.example.local
  port: 1883
  topic_prefix: frigate
  client_id: frigate
  user: "{FRIGATE_MQTT_USER}"
  password: "{FRIGATE_MQTT_PASSWORD}"
  stats_interval: 60
  qos: 0

telemetry:
  stats:
    nvidia_gpu_stats: true
  version_check: true
```

MQTT TLS certificate paths may be absolute Windows paths. Native GPU telemetry is provided through NVIDIA NVML when available; Intel and AMD adapters are still discovered for configuration and acceleration but do not report invented utilization values.

## Validate

```powershell
.\.venv\Scripts\python.exe -m frigate --validate-config
```

Validation checks model/device compatibility, case-insensitive names, Windows paths and reserved names, ports, proxy security, FFmpeg arguments, stream roles, zones, and cross-feature dependencies before processes start.
