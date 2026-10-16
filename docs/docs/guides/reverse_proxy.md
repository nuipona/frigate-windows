---
id: reverse_proxy
title: Reverse Proxy
---

# Windows reverse proxy

Frigate already provides an authenticated HTTPS listener on port `8971`. A reverse proxy is optional and is useful for a managed certificate, a shared hostname, or an external identity provider.

## Safe topology

Run IIS, Caddy, or another Windows proxy on the same machine or a trusted host. Forward to Frigate's **public** listener. Never publish the loopback-only internal listener on port `5000`.

The proxy must support:

- long-lived streaming responses;
- WebSocket upgrades for `/ws`, `/live`, and go2rtc routes;
- large uploads where model or configuration uploads are enabled;

Publish Frigate at the root of a dedicated hostname. The native gateway does not support hosting the UI beneath a path prefix.

If the proxy connects to Frigate over HTTPS with its generated certificate, configure the proxy to trust that certificate. Alternatively, bind Frigate's public listener to loopback with `FRIGATE_WEB_HOST=127.0.0.1`, disable Frigate TLS, and let the local proxy provide TLS. Do not use plain HTTP across an untrusted network.

## Forwarded client addresses

Frigate ignores caller-supplied forwarding data unless the immediate proxy is listed under `auth.trusted_proxies`. Add only the proxy's address or a narrow network:

```yaml
auth:
  trusted_proxies:
    - 127.0.0.1/32
    - ::1/128
```

The setting controls client-address resolution for login rate limiting; it does not bypass authentication.

## Identity-provider authentication

To let a proxy supply the authenticated user and role, disable native authentication and require a shared secret:

```yaml
auth:
  enabled: false
  roles:
    driveway_viewers:
      - driveway

proxy:
  header_map:
    user: X-Remote-User
    role: X-Remote-Groups
    role_map:
      admin:
        - frigate-admins
      driveway_viewers:
        - driveway-team
  default_role: none
  auth_secret: "{FRIGATE_PROXY_AUTH_SECRET}"
```

Set `FRIGATE_PROXY_AUTH_SECRET` in the Windows service environment or `secrets.yaml`. Configure the proxy to overwrite, not append, these headers and send:

```text
X-Proxy-Secret: <the same high-entropy secret>
X-Remote-User: <authenticated user>
X-Remote-Groups: <identity-provider groups>
```

When mapped proxy identity is active, the native gateway rejects a request without the matching secret. It strips client-supplied identity and secret headers before routing. Use `default_role: none` so an unknown group fails closed.

Do not put the shared secret in browser JavaScript, a query string, or a public proxy configuration repository.

## Firewall

If clients should connect only through the proxy:

1. bind Frigate to loopback when the proxy is local; or
2. restrict inbound port `8971` to the remote proxy address with Windows Firewall.

Keep go2rtc's management port and Frigate port `5000` off external interfaces.

## Validation checklist

- A direct request without a session or proxy secret is rejected.
- A mapped user receives only cameras granted to the resolved role.
- WebSockets connect after login.
- Live video and recorded HLS seek correctly.
- Logout clears the Frigate session or redirects to the identity provider.
- Login rate-limit logs show the actual client address rather than the proxy for every request.
