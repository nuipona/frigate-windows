---
id: home-assistant
title: Home Assistant Integration
---

# Home Assistant integration

Use the [official Frigate integration](https://github.com/blakeblackshear/frigate-hass-integration) from HACS.

## Prerequisites

Frigate must already be running on its Windows host. Install and configure Home Assistant's MQTT integration, then point both applications at the same broker:

```yaml
mqtt:
  enabled: true
  host: 192.168.1.10
  user: "{FRIGATE_MQTT_USER}"
  password: "{FRIGATE_MQTT_PASSWORD}"
```

Store the substituted values in Frigate's `secrets.yaml` or Windows service environment. MQTT supplies many entities and state updates even though API and media requests use HTTPS.

The Home Assistant `media_source` integration must be enabled for Frigate's Media Browser entries.

## Install

1. In HACS, search for and install **Frigate**.
2. Restart Home Assistant.
3. Open **Settings > Devices & services > Add integration > Frigate**.
4. Enter the native Windows URL, normally `https://<windows-host>:8971`.
5. Enter a dedicated Frigate username and password. Assign that user only the cameras Home Assistant needs.

The current integration supports Frigate's native JWT login. Do not use port `5000`: it is an unauthenticated, loopback-only listener reserved for Frigate's own processes and cannot be opened to another machine.

## TLS

Frigate generates a self-signed certificate by default. Home Assistant will reject it while certificate validation is enabled. Prefer installing a certificate whose authority Home Assistant trusts. If that is not immediately possible, the integration can disable certificate validation for this connection, but traffic should remain on a trusted network.

If a reverse proxy terminates TLS, enter its root URL and choose the matching authentication mode in the integration. Frigate does not support a URL path prefix, so use a dedicated hostname.

## Network access

Allow Home Assistant to reach TCP port `8971` on the Windows host. Limit the Windows Firewall rule to the Home Assistant address where practical. Live camera entities may also require Frigate's RTSP/WebRTC ports, depending on the configured stream method.

The Windows browser client, not Home Assistant, handles normal Frigate WebRTC candidates. Never advertise `localhost` to a client on another device.

## Optional dashboard card

The Frigate integration and the third-party Frigate dashboard card are separate. Follow the card's own installation instructions if you choose to use it.

## Options

```
Home Assistant > Configuration > Integrations > Frigate > Options
```

| Option            | Description                                                                                                                                                                                                                                                                                                                              |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RTSP URL Template | A [jinja2](https://jinja.palletsprojects.com/) template that is used to override the standard RTSP stream URL (e.g. for use with reverse proxies). This option is only shown to users who have [advanced mode](https://www.home-assistant.io/blog/2019/07/17/release-96/#advanced-mode) enabled. See [RTSP streams](#rtsp-stream) below. |

## Entities Provided

| Platform        | Description                                                                     |
| --------------- | ------------------------------------------------------------------------------- |
| `camera`        | Live camera stream (requires RTSP).                                             |
| `image`         | Image of the latest detected object for each camera.                            |
| `sensor`        | States to monitor Frigate performance, object counts for all zones and cameras. |
| `switch`        | Switch entities to toggle detection, recordings and snapshots.                  |
| `binary_sensor` | A "motion" binary sensor entity per camera/zone/object.                         |

## Media Browser Support

The integration provides:

- Browsing tracked object recordings with thumbnails
- Browsing snapshots
- Browsing recordings by month, day, camera, time

This is accessible via "Media Browser" on the left menu panel in Home Assistant.

## Casting Clips To Media Devices

The integration supports casting clips and camera streams to supported media devices.

:::tip
For clips to be castable to media devices, audio is required and may need to be [enabled for recordings](../troubleshooting/faqs.md#audio-in-recordings).

**NOTE: Even if you camera does not support audio, audio will need to be enabled for Casting to be accepted.**

:::

<a name="api"></a>

## Camera API

To turn a camera off (pauses Frigate's processing and persists the runtime state across restarts; see [Camera state](/configuration/live#camera-state)):

```
action: camera.turn_off
data: {}
target:
  entity_id: camera.back_deck_cam  # your Frigate camera entity ID
```

To turn a camera back on:

```
action: camera.turn_on
data: {}
target:
  entity_id: camera.back_deck_cam  # your Frigate camera entity ID
```

:::note

These actions toggle Frigate's runtime On/Off state. To permanently disable a camera, set its status to **Disabled** in **Settings → Camera Management** in the Frigate UI.

:::

## Notification API

Many people do not want to expose Frigate to the web, so the integration creates some public API endpoints that can be used for notifications.

To load a thumbnail for a tracked object:

```
https://HA_URL/api/frigate/notifications/<event-id>/thumbnail.jpg
```

To load a snapshot for a tracked object:

```
https://HA_URL/api/frigate/notifications/<event-id>/snapshot.jpg
```

To load a video clip of a tracked object using an Android device:

```
https://HA_URL/api/frigate/notifications/<event-id>/clip.mp4
```

To load a video clip of a tracked object using an iOS device:

```
https://HA_URL/api/frigate/notifications/<event-id>/master.m3u8
```

To load a preview gif of a tracked object:

```
https://HA_URL/api/frigate/notifications/<event-id>/event_preview.gif
```

To load a preview gif of a review item:

```
https://HA_URL/api/frigate/notifications/<review-id>/review_preview.gif
```

To load the thumbnail of a review item:

```
https://HA_URL/api/frigate/notifications/<review-id>/<camera>/review_thumbnail.webp
```

<a name="streams"></a>

## RTSP stream

In order for the live streams to function they need to be accessible on the RTSP
port (default: `8554`) at `<frigatehost>:8554`. Home Assistant will directly
connect to that streaming port when the live camera is viewed.

#### RTSP URL Template

For advanced usecases, this behavior can be changed with the [RTSP URL
template](#options) option. When set, this string will override the default stream
address that is derived from the default behavior described above. This option supports
[jinja2 templates](https://jinja.palletsprojects.com/) and has the `camera` dict
variables from [Frigate API](/integrations/api/frigate-http-api)
available for the template. Note that no Home Assistant state is available to the
template, only the camera dict from Frigate.

This is potentially useful when Frigate is behind a reverse proxy, and/or when
the default stream port is otherwise not accessible to Home Assistant (e.g.
firewall rules).

###### RTSP URL Template Examples

Use a different port number:

```
rtsp://<frigate_host>:2000/front_door
```

Use the camera name in the stream URL:

```
rtsp://<frigate_host>:2000/{{ name }}
```

Use the camera name in the stream URL, converting it to lowercase first:

```
rtsp://<frigate_host>:2000/{{ name|lower }}
```

## Multiple Instance Support

The Frigate integration seamlessly supports the use of multiple Frigate servers.

### Requirements for Multiple Instances

In order for multiple Frigate instances to function correctly, the
`topic_prefix` and `client_id` parameters must be set differently per server.
See [MQTT
configuration](mqtt)
for how to set these.

#### API URLs

When multiple Frigate instances are configured, [API](#notification-api) URLs should include an
identifier to tell Home Assistant which Frigate instance to refer to. The
identifier used is the MQTT `client_id` parameter included in the configuration,
and is used like so:

```
https://HA_URL/api/frigate/<client-id>/notifications/<event-id>/thumbnail.jpg
```

```
https://HA_URL/api/frigate/<client-id>/clips/front_door-1624599978.427826-976jaa.mp4
```

#### Default Treatment

When a single Frigate instance is configured, the `client-id` parameter need not
be specified in URLs/identifiers -- that single instance is assumed. When
multiple Frigate instances are configured, the user **must** explicitly specify
which server they are referring to.

## FAQ

#### If I am detecting multiple objects, how do I assign the correct `binary_sensor` to the camera in HomeKit?

The [HomeKit integration](https://www.home-assistant.io/integrations/homekit/) randomly links one of the binary sensors (motion sensor entities) grouped with the camera device in Home Assistant. You can specify a `linked_motion_sensor` in the Home Assistant [HomeKit configuration](https://www.home-assistant.io/integrations/homekit/#linked_motion_sensor) for each camera.

#### I have set up automations based on the occupancy sensors. Sometimes the automation runs because the sensors are turned on, but then I look at Frigate I can't find the object that triggered the sensor. Is this a bug?

No. The occupancy sensors have fewer checks in place because they are often used for things like turning the lights on where latency needs to be as low as possible. So false positives can sometimes trigger these sensors. If you want false positive filtering, you should use an mqtt sensor on the `frigate/events` or `frigate/reviews` topic.
