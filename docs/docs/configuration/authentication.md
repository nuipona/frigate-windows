---
id: authentication
title: Authentication
---

# Authentication and network access

Frigate's native Windows gateway exposes two HTTP listeners:

| Port | Binding | Authentication |
| --- | --- | --- |
| `8971` | Public interfaces by default | Required |
| `5000` | Loopback only | Not required |

The ports must be different. The internal listener is intended only for local integrations on the same Windows machine. Frigate refuses to expose it on a non-loopback address.

## Users and roles

The first-run flow creates an administrator. Administrators can create users and assign roles in the UI. Built-in access checks distinguish administrators, unrestricted viewers, and camera-scoped roles. Camera restrictions apply to REST media, exports, WebSocket events, live streams, and go2rtc proxy routes.

Use a strong JWT secret and keep the config directory readable only by the account running Frigate. Sessions issued before a secret change become invalid.

## TLS

Configure Frigate TLS in `config.yml` or place Frigate behind a trusted Windows reverse proxy. When TLS is disabled in Frigate, the public listener still requires authentication but traffic is not encrypted; expose it only on a trusted network or through a TLS proxy.

## Reverse proxy identity

A reverse proxy may supply mapped identity or role headers only when all of these controls are configured:

1. the configured identity/role header names are valid HTTP header names;
2. `proxy.auth_secret` is set;
3. the proxy sends the matching `X-Proxy-Secret` header on every mapped-identity request.

Example:

```yaml
proxy:
  header_map:
    user: X-Remote-User
    role: X-Remote-Role
  auth_secret: ${FRIGATE_PROXY_AUTH_SECRET}
```

Set the secret through the process environment or `secrets.yaml`; do not commit it. Frigate removes untrusted forwarded identity, address, credential, and proxy-secret headers before routing a request. A client cannot become trusted merely by supplying `X-Forwarded-For`.

## Proxy URL

Publish Frigate at the root of a dedicated hostname. The native Windows gateway does not currently support hosting the UI beneath a URL path prefix.

## Security recommendations

- Do not publish port 5000 through a firewall or port-forward rule.
- Restrict the Windows Firewall rule for port 8971 to the required networks.
- Prefer HTTPS for every non-loopback request.
- Give proxy and service accounts the minimum filesystem permissions required.
- Keep go2rtc's management API on loopback unless separately secured.
- Review account and role changes after restoring an older database.
