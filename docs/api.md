# HTTP API

Base URL: `http(s)://<server>:8080`.

**Authentication.** Use one of:
- a browser session (sign in at `/login`). State-changing requests (`POST`) must then also send the header `X-Requested-With: scrcpy-web`.
- `Authorization: Bearer <API_TOKEN>`, which needs no extra header.

**Responses.** Bodies are JSON (except screenshots). Errors look like `{"ok": false, "error": "…"}` with status `400` (bad input), `401` (not signed in), `403` (missing CSRF header, or a client/host name outside `ALLOWED_NETWORKS`/`ALLOWED_HOSTS`), `429` (too many wrong passwords or tokens from your address; wait and retry) or `503` (the phone is not connected or ADB failed).

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/healthz` | — | Liveness check, no auth needed. |
| GET | `/api/status` | — | Phone connection, model, Android version, battery, screen and lock state, whether mirroring is running. |
| GET | `/api/config` | — | UI settings (version, whether an unlock PIN is configured, valid key names). |
| POST | `/api/wake` | — | Turn the screen on. |
| POST | `/api/sleep` | — | Turn the screen off. |
| POST | `/api/unlock` | `{"pin": "1234"}` (optional) | Wake, swipe up and enter the PIN (`UNLOCK_PIN` is used when no PIN is sent). Returns `{"locked": false}` on success; `locked` is `null` if the phone doesn't report it. |
| POST | `/api/key` | `{"key": "home"}` | Press a key. Also `POST /api/key/<name>` with no body. |
| POST | `/api/text` | `{"text": "hello"}` | Type text with ADB (printable ASCII; newlines press Enter). |
| GET | `/api/screenshot` | — | PNG of the phone screen. Add `?download=1` to download it. |
| POST | `/api/restart` | — | Restart scrcpy (mirroring). |
| GET | `/websockify` | — | WebSocket carrying VNC (used by the web UI). |

**Key names:** `home`, `back`, `recents`, `power`, `wakeup`, `sleep`, `volume_up`, `volume_down`, `volume_mute`, `menu`, `enter`, `delete`, `tab`, `escape`, `play_pause`, `next`, `previous`, `camera`, `notifications`.

## Example `/api/status`

```json
{
  "ok": true,
  "mirroring": true,
  "version": "2.0.0",
  "device": {
    "state": "connected",
    "serial": null,
    "devices": [{"serial": "LGH870abc123", "state": "device", "model": "LG_H870"}],
    "manufacturer": "LGE",
    "model": "LG-H870",
    "android": "8.0.0",
    "sdk": 26,
    "battery": {"level": 87, "charging": true, "full": false, "plugged": true, "temperature_c": 29.1},
    "screen_on": true,
    "locked": false
  }
}
```

`device.state` is one of `connected`, `disconnected`, `unauthorized` (accept the USB debugging prompt), `offline`, `multiple` (set `ADB_SERIAL`) or `error`.

## iOS Shortcuts

Use the **Get Contents of URL** action:
- URL: `https://<server>/api/wake` (or `/api/unlock`)
- Method: `POST`
- Headers: `Authorization` = `Bearer <your API_TOKEN>`

## Home Assistant

Home Assistant must be on your local network (or in `ALLOWED_NETWORKS`).

```yaml
rest_command:
  phone_wake:
    url: "http://server:8080/api/wake"
    method: post
    headers:
      Authorization: !secret scrcpy_web_bearer   # "Bearer <token>"
  phone_key:
    url: "http://server:8080/api/key"
    method: post
    headers:
      Authorization: !secret scrcpy_web_bearer
    content_type: application/json
    payload: '{"key": "{{ key }}"}'

sensor:
  - platform: rest
    name: Phone battery
    resource: "http://server:8080/api/status"
    headers:
      Authorization: !secret scrcpy_web_bearer
    value_template: "{{ value_json.device.battery.level }}"
    unit_of_measurement: "%"
    scan_interval: 300
```

To embed the live screen in a dashboard iframe, set `ALLOW_FRAMING=true`.
