---
id: homekit
title: HomeKit
---

# Apple HomeKit

The bundled Windows `go2rtc.exe` can publish Frigate restreams as HomeKit camera accessories.

## Requirements

- The Windows host and Apple home hub/device must share a multicast-capable LAN.
- Windows Firewall must allow go2rtc's HomeKit/mDNS traffic on the selected network profile.
- The exported stream must be compatible with HomeKit, normally H.264 video at a supported resolution and Opus mono audio at 16 kHz.
- A stream must already exist under `go2rtc.streams`.

This publishes a Frigate stream to HomeKit. Importing an existing HomeKit camera as a Frigate source is a different go2rtc feature.

## Persistent configuration

HomeKit pairings are writable state, so they are stored separately from generated go2rtc settings:

```text
<Frigate config directory>\go2rtc_homekit.yml
```

For the default service install this is `%ProgramData%\Frigate\config\go2rtc_homekit.yml`. Frigate keeps only the `homekit` section and passes this file to go2rtc as its writable primary config. Do not store camera URLs or other settings there.

Open the go2rtc editor through Frigate's authenticated gateway:

```text
https://frigate-host:8971/go2rtc/editor.html
```

The go2rtc management port is loopback-only by default and should not be exposed directly.

Add an entry whose key exactly matches a configured stream:

```yaml
homekit:
  front_door:
    name: Front Door
    pin: "12345678"
```

Use a unique eight-digit PIN that is not an obvious sequence. Protect this file like other credentials because it contains pairing state.

## Compatible restream

If the camera's main stream is not HomeKit-compatible, define a dedicated stream in `config.yml`:

```yaml
go2rtc:
  streams:
    front_door:
      - rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera/main
    front_door_homekit:
      - "ffmpeg:front_door#video=h264#width=1280#height=720#audio=opus/16000"
```

Then use `front_door_homekit` as the key in `go2rtc_homekit.yml`. On matching NVIDIA or Windows decode hardware, go2rtc can use `#hardware=cuda` or `#hardware=dxva2`; test software transcoding first.

## Pair

1. Restart Frigate after adding the `homekit` entry.
2. In Apple Home, choose **Add Accessory**, then **More options**.
3. Select the camera and enter the configured PIN.
4. Confirm a `pairings` value is written beneath the camera in `go2rtc_homekit.yml`.

If the accessory is not discovered, verify that the Windows network is Private, multicast is not isolated by the access point/VLAN, and Windows Firewall allows go2rtc. If pairing succeeds but fails after restart, check that the Frigate service account can modify `go2rtc_homekit.yml` and inspect `logs\go2rtc.log` for a save error.

Renaming the stream changes the accessory identity and requires pairing again.
