# Security

scrcpy-web gives full control of a phone, so treat access to it like access to the unlocked phone.

## Defaults

- The web UI and API require a password; if none is configured, a random one is generated. Logins are rate limited per IP.
- Sessions are HMAC-signed `HttpOnly`, `SameSite=Strict` cookies. Changing the password invalidates them.
- State-changing API calls from a browser need a custom header (CSRF protection). WebSocket connections from other origins are refused.
- Responses send a strict Content-Security-Policy and refuse framing (unless `ALLOW_FRAMING=true`).
- The VNC server only listens on a private unix socket unless `VNC_PASSWORD` is set. Native VNC always requires a password.
- The ADB server only listens inside the container unless `ADB_EXPOSE=true`.
- Each installation generates its own ADB key in `/data`. No key ships in the image.
- The container runs with all Linux capabilities dropped and `no-new-privileges`. USB access is granted with a device cgroup rule, not `--privileged`.
- The unlock PIN is never logged. A PIN typed into the UI is not stored.

## Recommendations

- Don't expose port 8080 directly to the internet. Use Tailscale (`tailscale serve` gives you HTTPS) or another VPN, or a reverse proxy with HTTPS.
- Docker-published ports bypass `ufw`. Bind ports to `127.0.0.1` or a VPN address.
- Native VNC (`5900`) is unencrypted and its passwords are at most 8 characters. Only use it on a private network or VPN.
- The exposed ADB server (`5037`) has no authentication at all. Only enable it on a trusted network.
- Use a long random `API_TOKEN`.

## Reporting a vulnerability

Please open a [private security advisory](https://github.com/ifloofie/scrcpy-web/security/advisories/new) rather than a public issue.
