---
id: live
header_title: Live View
---

# Live view

Frigate uses the bundled Windows `go2rtc.exe` for low-latency live viewing. Configure streams in `config.yml`:

```yaml
go2rtc:
  streams:
    front:
      - rtsp://user:password@camera/main
```

A stream name matching the camera name is selected automatically for that camera's live view. Additional streams can be selected in the UI.

## Stream types

The UI chooses among supported delivery methods based on browser and network capabilities:

- **WebRTC** — lowest latency; requires a valid candidate address and reachable UDP/TCP ports.
- **MSE** — broadly useful on a LAN and carried over the authenticated Frigate gateway.
- **JSMPEG** — fallback for otherwise incompatible sources; higher bandwidth and resource use.

Prefer H.264 video and AAC or Opus audio for the widest browser compatibility. H.265 support depends on browser, Windows codec availability, and hardware.

## Restreaming

Other applications can consume a configured go2rtc stream from the Frigate host. Keep management endpoints loopback-only and expose only the media routes actually needed. When Frigate is behind a reverse proxy, forward WebSocket upgrades and use Frigate's authenticated public port rather than exposing the internal gateway.

## WebRTC candidates

If automatic candidate discovery is not sufficient, configure addresses that browsers can reach:

```yaml
go2rtc:
  webrtc:
    candidates:
      - 192.168.1.20:8555
      - stun:8555
```

Permit the configured TCP and UDP ports in Windows Firewall only from required networks. Do not use `localhost` as a candidate for remote browsers.

## Troubleshooting

1. Confirm `go2rtc.exe` is running and the stream is healthy in Frigate diagnostics.
2. Verify camera credentials and codec compatibility.
3. Test MSE before diagnosing WebRTC routing.
4. Check Windows Firewall and NAT rules for WebRTC candidates.
5. If Frigate runs as a service, remember it has a different environment and network credentials from the signed-in desktop user.

The browser developer console and `logs\go2rtc.log` usually show whether a failure is authentication, codec negotiation, or connectivity related.
