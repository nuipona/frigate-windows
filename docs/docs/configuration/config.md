---
id: config
title: Frigate Configuration
---

import NavPath from "@site/src/components/NavPath";

Frigate can be configured through the **Settings UI** or by editing YAML. The Settings UI is recommended because it validates values and explains which changes require a restart.

## Configuration file

The native Windows configuration directory defaults to:

```text
%LOCALAPPDATA%\Frigate\config
```

The service installer can select another data root. In that case, use its `config` subdirectory. Frigate accepts `config.yml` or `config.yaml`; if both exist, `config.yml` wins.

A minimal starting configuration is:

```yaml
mqtt:
  enabled: false

cameras:
  front:
    enabled: false
    ffmpeg:
      inputs:
        - path: rtsp://user:password@camera/stream
          roles: [detect]
```

The built-in default object detector uses the CPU. Add only settings that differ from defaults.

## Settings UI

Global values under <NavPath path="Settings > Global configuration" /> apply to every camera. Values under <NavPath path="Settings > Camera configuration" /> override the corresponding global value for the selected camera.

Pending edits are not applied until saved. **Save** writes the current section, while **Save All** writes pending changes across sections and cameras. A restart indicator identifies settings that cannot be applied dynamically. Use **Reset to Global** to remove a camera override or **Reset to Default** at global scope.

## Editing safely

Before editing manually:

1. stop the Frigate Windows service, or use the editor built into the UI;
2. make a backup of `config.yml`, `secrets.yaml`, and `frigate.db`;
3. preserve YAML indentation with spaces;
4. validate before restarting.

From the installation directory:

```powershell
.\.venv\Scripts\python.exe -m frigate --validate-config
```

## VS Code schema

The authenticated public API serves the configuration schema. Add this first line and replace the host as needed:

```yaml
# yaml-language-server: $schema=https://frigate-host:8971/api/config/schema.json
```

If a certificate is self-signed, VS Code must trust that certificate. Never expose the unauthenticated loopback listener to retrieve the schema remotely.

## Environment substitutions and secrets

Values marked as supporting substitution in the [reference configuration](./advanced/reference.md) can use names beginning with `FRIGATE_`:

```yaml
mqtt:
  host: "{FRIGATE_MQTT_HOST}"
  user: "{FRIGATE_MQTT_USER}"
  password: "{FRIGATE_MQTT_PASSWORD}"

cameras:
  front:
    ffmpeg:
      inputs:
        - path: rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera/stream
          roles: [detect, record]
```

Substitution values are read, in precedence order, from the process environment, `secrets.yaml`, and `environment_vars`. Protect `secrets.yaml` with a Windows ACL and do not commit it.

## Native Windows example

This example uses Direct3D 11 video decoding, CPU object detection, motion recording, and snapshots:

```yaml
mqtt:
  enabled: false

ffmpeg:
  hwaccel_args: preset-d3d11va

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
      days: 30
      mode: motion

snapshots:
  enabled: true
  retain:
    default: 30

cameras:
  front:
    detect:
      width: 1280
      height: 720
      fps: 5
    ffmpeg:
      inputs:
        - path: rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera/substream
          roles: [detect]
        - path: rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera/mainstream
          roles: [record]
```

For Intel inference, configure an OpenVINO model and a device such as `openvino:AUTO`. For ONNX inference, use `onnx:cpu`, `onnx:directml`, or `onnx:cuda` according to the installed provider. See [Object Detectors](./object_detectors.md).

## Windows-specific validation

Frigate rejects:

- detector and acceleration backends not supported by the native Windows build;
- camera names that differ only by letter case;
- Windows reserved device names such as `CON` and `NUL`;
- identical public and internal HTTP ports;
- a non-loopback internal listener;
- unsafe reverse-proxy identity mappings.

Use the [full reference](./advanced/reference.md) for all options and [system configuration](./advanced/system.md) for paths, ports, and service behavior.
