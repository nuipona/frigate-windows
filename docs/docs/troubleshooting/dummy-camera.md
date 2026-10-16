---
id: dummy-camera
title: Test Camera
---

# Test with a local video file

A looped local recording helps separate Frigate configuration problems from camera networking.

## Prepare a sample

Copy an H.264 MP4 file to a short local path such as:

```text
C:\FrigateSamples\camera.mp4
```

The account running Frigate must have read access. Do not use a mapped drive letter for a Windows service.

## Configure the camera

```yaml
cameras:
  test_camera:
    ffmpeg:
      inputs:
        - path: 'C:\FrigateSamples\camera.mp4'
          input_args: -re -stream_loop -1 -fflags +genpts
          roles: [detect, record]
    detect:
      width: 1280
      height: 720
      fps: 5
```

Set width and height to the sample's actual decoded dimensions. Disable recording if it is not part of the test.

## Inspect the file

Use the bundled tools:

```powershell
$ffmpeg = "$env:LOCALAPPDATA\Frigate\ffmpeg\bin"
& "$ffmpeg\ffprobe.exe" -hide_banner 'C:\FrigateSamples\camera.mp4'
& "$ffmpeg\ffplay.exe" -stream_loop -1 'C:\FrigateSamples\camera.mp4'
```

For a service installation with another base directory, adjust the executable path.

## Interpret the result

- If the local file works but the camera does not, investigate the URL, credentials, network, timestamps, or camera codec.
- If both fail, inspect Frigate's generated FFmpeg command, detector startup, paths, and service permissions.
- If software decode works but a Windows preset fails, troubleshoot the GPU driver and selected backend.

Remove the test camera and generated recordings after diagnosis; a looped file continues creating media while recording is enabled.
