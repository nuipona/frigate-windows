---
id: camera_specific
title: Camera Specific Configurations
---

# Camera-specific configuration

## RTSP cameras

Use a dedicated substream for detection when available and the main stream for recording:

```yaml
cameras:
  front:
    ffmpeg:
      inputs:
        - path: rtsp://user:password@camera/low
          roles: [detect]
        - path: rtsp://user:password@camera/high
          roles: [record]
```

If the camera limits concurrent connections, restream it through Frigate's bundled go2rtc process and point Frigate and other clients at the local restream. Keep go2rtc's management API on loopback.

## HTTP cameras

For an HTTP MJPEG stream, select an input preset appropriate to the source:

```yaml
cameras:
  driveway:
    ffmpeg:
      inputs:
        - path: http://camera/video.mjpg
          input_args: preset-http-mjpeg-generic
          roles: [detect]
```

A JPEG snapshot URL is not a video stream and should not be configured as a camera input.

## Reolink

Prefer the camera's RTSP stream when current firmware provides a stable feed. For HTTP-FLV sources, specify the compatible input preset and test audio separately. Avoid opening several independent high-resolution sessions to the same camera; use go2rtc restreaming instead.

## Hikvision

Hikvision channel URLs commonly use this pattern:

```text
rtsp://user:password@camera:554/Streaming/Channels/101
```

`101` normally identifies the first channel's main stream and `102` its substream. Exact channel numbers and authentication settings vary by firmware.

## Blue Iris

Frigate can consume a Blue Iris RTSP stream. Use the Blue Iris machine's LAN address, not `localhost`, unless both applications run on the same Windows host:

```yaml
cameras:
  driveway:
    ffmpeg:
      inputs:
        - path: rtsp://user:password@blue-iris-host:81/driveway
          roles: [detect, record]
```

Use short camera names without spaces. Frigate treats names case-insensitively on Windows, so `FrontDoor` and `frontdoor` cannot both be configured.

## Local video files

Local file inputs are useful for diagnostics:

```yaml
cameras:
  test:
    ffmpeg:
      inputs:
        - path: C:\Frigate\samples\camera.mp4
          input_args: -re -stream_loop -1 -fflags +genpts
          roles: [detect]
```

Use an absolute Windows path. The service account must have read access to the file and every parent directory.

## Timestamps and overlays

Camera timestamps are useful for correlating source frames with Frigate events, but they should not obscure detection regions. Configure overlays in the camera firmware where possible. Frigate stores event times in UTC and presents them using the configured UI timezone.

## Troubleshooting

1. Test the exact URL with the installed `ffplay.exe` or VLC from the Frigate machine.
2. Verify the service account can resolve the camera hostname and reach the port.
3. Check that only one detection stream is assigned per camera.
4. Start without hardware acceleration, then enable a Windows preset after basic decoding works.
5. Review `logs\frigate.log` and copy the generated FFmpeg command when requesting help.

Never publish camera credentials or complete signed URLs in logs, screenshots, or support requests.
