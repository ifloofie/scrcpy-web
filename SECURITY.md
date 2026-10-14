# Security

scrcpy-web gives full control of a phone, so treat access to it like access to the unlocked phone.

## Defaults

- **Local network only.** The compose file publishes the web UI only on `BIND_ADDRESS` (your LAN IP). The server refuses clients outside private address ranges (`ALLOWED_NETWORKS`) and requests under non-local host names (`ALLOWED_HOSTS`, which blocks DNS rebinding).
- The web UI and API require a password; if none is configured, a random one is generated. Wrong passwords **and wrong API tokens** are rate limited per IP: after 5 failures, a lockout of 30 s that doubles each time, up to 1 hour.
- The login page only shows fixed messages; the URL can't inject text into it. The post-login redirect only accepts local paths.
- The `Server` header doesn't reveal software versions. HSTS is sent when serving HTTPS directly.
- Sessions are HMAC-signed `HttpOnly`, `SameSite=Strict` cookies. Changing the password invalidates them.
- State-changing API calls from a browser need a custom header (CSRF protection). WebSocket connections from other origins are refused.
- Responses send a strict Content-Security-Policy and refuse framing (unless `ALLOW_FRAMING=true`).
- The VNC server only listens on a private unix socket unless `VNC_PASSWORD` is set. Native VNC always requires a password.
- The ADB server only listens inside the container unless `ADB_EXPOSE=true`.
- Each installation generates its own ADB key in `/data`. No key ships in the image.
- The container runs with all Linux capabilities dropped and `no-new-privileges`. USB access is granted with a device cgroup rule, not `--privileged`.
- The unlock PIN is never logged. A PIN typed into the UI is not stored.

## Known limits

- **Plain HTTP on the LAN.** Without `TLS_CERT`/`TLS_KEY` (or Tailscale), the password and session cookie cross your network unencrypted. Use HTTPS if untrusted devices share your network.
- **Native VNC (5900) and remote ADB (5037)** aren't covered by the address and host-name checks; only the bind address protects them. ADB has no authentication at all.
- **Session revocation.** Signing out deletes the browser's cookie. To invalidate *every* session (e.g. after a lost device), change `WEB_PASSWORD`, or delete `/data/session-secret` and restart.
- **USB access.** The container can open any USB device on the host (needed so the phone is found again after a replug). This only matters if the container itself were compromised.
- The address check sees the address Docker hands to the container. With the compose file's IPv4 `BIND_ADDRESS` that's the real client. If you publish the port on IPv6 or behind a proxy, every client may appear as the Docker gateway (a private address), so rely on the bind address and your router.

## Recommendations

- Never forward 8080, 5900 or 5037 on your router, and don't use `tailscale funnel`. For access away from home, use a private VPN such as Tailscale (`tailscale serve`).
- Docker-published ports bypass `ufw`. Always publish on a specific address (`BIND_ADDRESS`), never a bare `8080:8080`.
- Narrow `ALLOWED_NETWORKS` to your own subnet (e.g. `192.168.1.0/24`).
- Native VNC (`5900`) is unencrypted and its passwords are at most 8 characters. Only use it on a private network or VPN.
- The exposed ADB server (`5037`) has no authentication at all. Only enable it on a trusted network.
- Use a long random `API_TOKEN`.

## Reporting a vulnerability

Please open a [private security advisory](https://github.com/ifloofie/scrcpy-web/security/advisories/new) rather than a public issue.
