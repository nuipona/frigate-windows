---
id: tls
title: TLS
---

# TLS

Frigate's native Windows gateway can serve HTTPS directly on the authenticated public listener (port `8971` by default).

```yaml
tls:
  enabled: true
```

On first startup, Frigate creates a self-signed certificate beneath the Windows configuration directory. Browsers will warn until that certificate or its issuing authority is trusted.

## Custom certificate

Configure an unencrypted PEM certificate chain and private key, then restart Frigate:

```yaml
tls:
  enabled: true
  cert_file: 'C:\ProgramData\Frigate\config\tls\fullchain.pem'
  key_file: 'C:\ProgramData\Frigate\config\tls\privkey.pem'
```

When both configured files are absent, Frigate generates a self-signed pair. If only one exists, startup fails rather than overwriting certificate material. The service account must have read access to the private key; other local users should not.

A certificate should include every DNS name or IP address clients use in its Subject Alternative Name extension. Keep an unencrypted deployment key only when its ACL is restricted to Administrators, SYSTEM, and the Frigate service account.

## Reverse-proxy TLS

A trusted Windows reverse proxy may terminate TLS instead. In that design:

- proxy only to Frigate's authenticated public listener unless the proxy runs on the same host and the loopback trust boundary is intentional;
- preserve WebSocket upgrades and streaming responses;
- configure proxy identity mapping only with a shared `proxy.auth_secret`;
- strip client-supplied forwarded identity and secret headers;
- restrict Frigate's public firewall rule to the proxy when possible.

## Disabling TLS

```yaml
tls:
  enabled: false
```

Disabling TLS does not disable authentication. Use plain HTTP only on loopback or a trusted network behind a TLS-terminating proxy. Credentials and session cookies are otherwise exposed in transit.

## Certificate renewal

Renew custom certificates before expiration, replace the files atomically, and restart the Windows service. Verify the complete certificate chain from another machine after renewal.
