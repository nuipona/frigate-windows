---
id: go2rtc
title: go2rtc Troubleshooting
---

# go2rtc troubleshooting on Windows

go2rtc powers MSE, WebRTC, restreaming, and two-way talk. Frigate starts `go2rtc.exe` as a managed child and writes its output to `logs\go2rtc.log`.

## 1. Check the process and log

```powershell
Get-Process go2rtc -ErrorAction SilentlyContinue
Get-Content "$env:ProgramData\Frigate\logs\go2rtc.log" -Tail 200
```

Adjust the path for an interactive or custom data root. If the executable is missing, rerun `bootstrap_windows.py --skip-web`. Antivirus quarantine is a common cause of a file disappearing after bootstrap.

## 2. Inspect streams safely

Open the management UI through Frigate's authenticated gateway:

```text
https://frigate-host:8971/go2rtc/
```

Do not expose port `1984`; the generated native config binds it to loopback. Stream diagnostics can contain camera credentials, so redact them before sharing.

## 3. Test the source

Test the exact camera URL in VLC or Frigate's bundled `ffplay.exe`. Confirm:

- the service account can resolve and reach the camera;
- credentials are correct;
- the camera has not reached its connection limit;
- the source has monotonic timestamps;
- video and audio codecs are supported by the browser or selected transcode.

If direct playback fails, fix the camera source before changing go2rtc.

## Live view works only in one browser

Use H.264 video and AAC or Opus audio for broad compatibility. H.265 support varies with browser, Windows codec installation, and hardware. MSE often succeeds where WebRTC networking fails; WebRTC often succeeds where an MSE codec is unsupported.

Enable a dedicated go2rtc FFmpeg transcode only when copying a compatible stream is not possible. Transcoding increases CPU/GPU use.

## WebRTC does not connect

1. Verify MSE works first.
2. Configure a candidate address reachable from the browser, not `localhost`.
3. Permit the configured WebRTC TCP and UDP port in Windows Firewall from required networks.
4. Check VLAN client isolation and NAT.
5. Use a STUN candidate only when outbound STUN is permitted.

```yaml
go2rtc:
  webrtc:
    candidates:
      - 192.168.1.20:8555
      - stun:8555
```

## No audio

Confirm the source actually contains audio. Browser-compatible options are usually AAC for MSE and Opus for WebRTC. Camera G.711 audio may need a go2rtc transcode such as `#audio=opus` or `#audio=aac` depending on the target.

## Two-way talk unavailable

Two-way talk needs a camera-supported backchannel and a compatible browser microphone codec. Any fragment on a bare RTSP source can disable backchannel detection unless `#backchannel=1` is explicit. It is often safer to configure two streams: one with `#backchannel=0` for ordinary viewing and one unmodified source for talkback.

Only one application may hold some camera backchannels. Close vendor apps while testing.

## Hardware transcode fails

Test software transcoding first. Native go2rtc choices exposed by Frigate are CUDA and DXVA2 plus automatic selection. Update the Windows display driver and verify the bundled FFmpeg reports the chosen backend.

## Configuration changes do not appear

Frigate regenerates its runtime go2rtc config at startup. Save `config.yml` and restart Frigate. HomeKit pairing state is the exception and persists in `config\go2rtc_homekit.yml`.

## Restricted source removed

`exec:`, `echo:`, `expr:`, and dynamic command sources are blocked by default. They require the protected `GO2RTC_ALLOW_ARBITRARY_EXEC=true` startup opt-in. Do not enable it for configuration managed by untrusted users.
