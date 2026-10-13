# scrcpy-web

Control an Android phone that is plugged into your server, from any browser (including iPhone Safari) or VNC app.

scrcpy-web runs [scrcpy](https://github.com/Genymobile/scrcpy) inside a Docker container with a virtual display, and serves it as a web page through [noVNC](https://github.com/novnc/noVNC). On top of the live screen you get buttons to wake and unlock the phone, Android navigation and volume keys, text typing and pasting, screenshots, and a small HTTP API for automations.

> This is a fork of [robotfishe/scrcpy-web](https://github.com/robotfishe/scrcpy-web), rebuilt to be maintained long term. See [what changed](#changes-from-the-original) and the [CHANGELOG](CHANGELOG.md).

## Features

- **Web UI on port 8080.** Works on phones, tablets and desktops, and can be added to an iPhone home screen as an app.
- **Wake and unlock** from a button, with an optional stored PIN. The phone can also wake automatically when you open the page.
- **Back, Home, Recents, Power, Volume** and notification shade buttons.
- **Typing and pasting text.** Paste handles any text, including emoji.
- **Screenshots** in one tap.
- **Rotation aware.** The view follows the phone between portrait and landscape with no black bars.
- **Robust.** Every service is supervised and restarted. The phone is picked up again after it reboots or is replugged, and `docker stop` is instant.
- **Secure by default.**
  - The web UI requires a password, with rate-limited login and CSRF protection.
  - Each install generates its own ADB key.
  - Raw VNC and ADB ports stay closed unless you enable them.
  - The container runs with every Linux capability dropped.
- **HTTP API** with bearer token support, for iOS Shortcuts, Home Assistant or scripts.
- **Native VNC** (optional) for VNC apps.
- **Remote ADB** (optional) so you can run scrcpy or adb on your PC against the phone on the server.
- **Kept current.** The image is rebuilt weekly for security updates, a workflow proposes new scrcpy releases automatically, and images are published for amd64 and arm64.

## Quick start

1. **On the phone**, enable Developer options: Settings › About phone › tap *Build number* 7 times. Then turn on **USB debugging** under Settings › Developer options, and plug the phone into the server.
2. **On the server:**
   ```sh
   git clone https://github.com/ifloofie/scrcpy-web.git && cd scrcpy-web
   cp .env.example .env        # set WEB_PASSWORD in here
   docker compose up -d
   ```
3. **Approve the phone.** Unlock it and accept *Allow USB debugging?*, ticking **Always allow from this computer**.
4. Open `http://<server>:8080` and sign in.

If you didn't set `WEB_PASSWORD`, a password was generated. Find it with `docker logs scrcpy-web | grep password`.

<details>
<summary>Plain <code>docker run</code> instead of compose</summary>

```sh
docker run -d --name scrcpy-web --restart unless-stopped \
  -p 8080:8080 \
  -e WEB_PASSWORD='choose-a-password' \
  -v scrcpy-web-data:/data \
  -v /dev/bus/usb:/dev/bus/usb \
  --device-cgroup-rule='c 189:* rmw' \
  --cap-drop ALL --security-opt no-new-privileges \
  ghcr.io/ifloofie/scrcpy-web:latest
```
</details>

### Unraid

Use the template in [`unraid/scrcpy-web.xml`](unraid/scrcpy-web.xml). In Docker › Add Container, paste the template's raw URL into *Template repositories* in Docker settings, or copy the file to `/boot/config/plugins/dockerMan/templates-user/`.

## Reaching it from anywhere

The easiest secure option is [Tailscale](https://tailscale.com). Bind the port to localhost and let Tailscale serve it over HTTPS with a real certificate:

```yaml
# docker-compose.yml
    ports:
      - "127.0.0.1:8080:8080"
```
```sh
sudo tailscale serve --bg 8080
```

Then open `https://<server-name>.<your-tailnet>.ts.net` on any device signed in to your tailnet. HTTPS also lets the iPhone treat it as a proper home-screen app: Share › *Add to Home Screen*.

> **Docker and firewalls.** Ports published by Docker bypass `ufw`. Don't rely on ufw to protect `8080`, `5900` or `5037`. Bind them to a specific address instead, such as `127.0.0.1` (with `tailscale serve`) or your Tailscale IP (`100.x.y.z:8080:8080`).

To serve HTTPS directly, mount a certificate and set `TLS_CERT` and `TLS_KEY`. Behind your own reverse proxy (Caddy, nginx, Traefik), forward WebSocket upgrades for `/websockify`.

## Using the web UI

| Button | What it does |
|---|---|
| ◁ ○ ▢ | Back, Home, Recent apps |
| ☀ | Wake the screen |
| 🔓 | Wake, swipe up and (optionally) enter your PIN |
| ⏻ | Power button |
| 🔉 🔊 | Volume |
| ⌨ | Send text: **Paste** works with any text; **Type** uses ADB key events (ASCII only) |
| 🔔 | Open the notification shade |
| 📷 | Download a screenshot |
| ⋯ | Picture quality, turn screen off, reconnect, restart mirroring, sign out |

Tap and drag on the screen to tap and swipe on the phone. On a computer, click the screen and type directly; your keyboard goes to the phone.

## Native VNC (optional)

Set `VNC_PASSWORD` (8 characters at most, a limit of the VNC protocol) and publish port `5900`. Any VNC app can then connect: RealVNC Viewer, Screens and others. VNC is unencrypted, so only use it over Tailscale or a LAN. When no VNC password is set, the VNC port isn't opened at all, and the web UI talks to the display over a private socket.

## Using scrcpy on your PC (optional)

Set `ADB_EXPOSE=true` and publish port `5037`. On the PC, install scrcpy and point it at the server's ADB:

```powershell
# Windows PowerShell
$env:ADB_SERVER_SOCKET = "tcp:<server-ip>:5037"
scrcpy
```

The ADB server has **no authentication**. Only expose it over Tailscale or a trusted LAN.

## Configuration

Every setting is an environment variable, and all are optional. Settings marked † also accept a `*_FILE` variant (for example `WEB_PASSWORD_FILE=/run/secrets/web_password`) for Docker secrets.

### Web UI and API

| Variable | Default | Description |
|---|---|---|
| `WEB_PASSWORD` † | *generated* | Password for the web UI. If unset, one is generated on first start, logged, and saved in `/data/web-password`. |
| `WEB_AUTH` | `password` | `none` disables the login. Only do this behind your own authenticating proxy. |
| `API_TOKEN` † | — | Lets scripts call the API with `Authorization: Bearer <token>`. |
| `UNLOCK_PIN` † | — | PIN or password the Unlock button enters. If unset, the UI asks each time and doesn't store it. |
| `WAKE_ON_CONNECT` | `true` | Wake the phone's screen when someone opens the web UI. |
| `SESSION_DAYS` | `30` | How long a sign-in lasts. Changing the password signs everyone out. |
| `WEB_PORT` | `8080` | Port of the web UI inside the container. |
| `TLS_CERT`, `TLS_KEY` | — | Paths to a certificate and key, to serve HTTPS directly. |
| `ALLOW_FRAMING` | `false` | Allow embedding in an iframe (e.g. a Home Assistant dashboard). |

### Phone and scrcpy

| Variable | Default | Description |
|---|---|---|
| `ADB_SERIAL` | — | Which device to use when more than one is connected (see `adb devices`). |
| `MAX_SIZE` | `1280` | Longest side of the video in pixels. `0` is the phone's full resolution. Lower values are smoother over slow connections. |
| `MAX_FPS` | `30` | Frame rate limit. |
| `VIDEO_BIT_RATE` | `8M` | Video bit rate between the phone and the server. |
| `VIDEO_CODEC` | `h264` | `h264`, `h265` or `av1` (the phone must support it). |
| `STAY_AWAKE` | `true` | Keep the phone awake while it's plugged in. |
| `SCREEN_OFF` | `false` | Turn the phone's own screen off while mirroring. Mirroring and control still work. |
| `SCRCPY_ARGS` | — | Extra [scrcpy flags](https://github.com/Genymobile/scrcpy/tree/master/doc), e.g. `--show-touches`. |

### Display, VNC and ADB

| Variable | Default | Description |
|---|---|---|
| `VNC_PASSWORD` † | — | Setting it opens the native VNC port. |
| `VNC_ENABLE` | *auto* | Force native VNC on or off. With `true` and no password, one is generated and logged. |
| `VNC_PORT` | `5900` | Native VNC port. |
| `VNC_SCREEN_SIZE` | *auto* | Fix the display size (e.g. `1080x1920`) instead of following the phone. Rotation is then letterboxed. |
| `ADB_EXPOSE` | `false` | Let other computers use this ADB server. |
| `ADB_PORT` | `5037` | ADB server port. |

### Data volume

`/data` holds the ADB key (so the phone's *Always allow* survives updates), generated passwords and the session signing secret. Keep it in a volume.

## HTTP API

Every endpoint needs a signed-in browser session or `Authorization: Bearer $API_TOKEN`. See [docs/api.md](docs/api.md) for the full reference.

```sh
TOKEN=your-api-token
curl -X POST -H "Authorization: Bearer $TOKEN" http://server:8080/api/wake
curl -X POST -H "Authorization: Bearer $TOKEN" http://server:8080/api/unlock
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"key":"home"}' http://server:8080/api/key
curl -H "Authorization: Bearer $TOKEN" http://server:8080/api/status
curl -H "Authorization: Bearer $TOKEN" -o shot.png http://server:8080/api/screenshot
```

## Troubleshooting

| Problem | Fix |
|---|---|
| "Waiting for the phone" | Check USB debugging is on and the cable carries data. Run `docker exec scrcpy-web adb devices`. If nothing is listed, check that `/dev/bus/usb` is mounted and the `c 189:* rmw` cgroup rule is set. |
| "Allow USB debugging" | Unlock the phone and accept the prompt. If it never appears: Developer options › *Revoke USB debugging authorizations*, then replug. |
| Phone not found after a reboot or replug | Usually a missing `device_cgroup_rules` entry. Older setups that used `--device=/dev/bus` lose the phone when its device node changes. |
| Picture shows but taps do nothing (Xiaomi/Redmi/POCO) | Also enable **USB debugging (Security settings)** in Developer options. |
| Laggy or blocky picture | Lower `MAX_SIZE` (e.g. `1024`) or `MAX_FPS`, or pick a lower quality in the ⋯ menu. |
| Unlock doesn't work | Set `UNLOCK_PIN`, or unlock by hand once. Lock-screen detection varies by Android version and skin. |
| Phone battery wear | Many phones (LG included) can limit charging to about 80% in battery settings. A smart plug on a timer also works. |
| Logs | `docker logs -f scrcpy-web` |

The phone needs Android 5.0 or newer, which is scrcpy's minimum.

## Limitations

- **No audio.** VNC carries no sound.
- **One phone per container.** Several phones on one host aren't supported yet, because each container's ADB server would compete for the same USB devices.
- **Typing via ADB is ASCII-only.** Use **Paste** for other text.

## Changes from the original

The original repository could no longer build. scrcpy now requires SDL3, and the build silently produced an image *without scrcpy* because errors in its `RUN` heredoc were ignored. It also shipped one ADB private key inside the public image, shared by every user. This fork fixes those problems and reworks the project:

| | Original | This fork |
|---|---|---|
| Builds today | ❌ (SDL3 missing; errors ignored) | ✅ scrcpy pinned to a release; build fails loudly; checksum-verified server |
| ADB key | one key baked into the public image | generated per install, kept in `/data` |
| Access | VNC only, **no password by default** | password-protected web UI; VNC optional and password-only |
| Phone reboot or replug | container must be restarted | picked up automatically |
| Crashed services | only scrcpy restarted | everything supervised |
| `docker stop` | hangs 10 s, then killed | ~2 s, clean |
| Rotation | cut off / manual size | display follows the phone |
| Extras | — | wake/unlock, keys, text, screenshots, API, HTTPS, PWA |
| Maintenance | manual | CI tests, weekly rebuilds, automatic scrcpy updates, amd64 + arm64 |

**Migrating** from `robotfishe/scrcpy-web`:
- Use the new image, add the `/data` volume, and swap `--device=/dev/bus` for the `/dev/bus/usb` volume plus the cgroup rule.
- Accept *Allow USB debugging* once more; the new install has its own key.
- `VNC_PASSWORD`, `VNC_SCREEN_SIZE` and `SCRCPY_ARGS` still work. `--no-audio` is no longer needed.
- Prefer leaving `VNC_SCREEN_SIZE` unset, so rotation is handled for you.

## Development

```sh
docker build -t scrcpy-web:test .
tests/run-unit-tests.sh scrcpy-web:test   # Python tests inside the image (fake adb)
tests/smoke.sh scrcpy-web:test            # end-to-end container test (no phone needed)
python3 -m unittest discover -s tests     # quick local run (needs aiohttp)
```

How it fits together:

```
tini → init.sh → supervisord ─┬─ Xvnc :1          virtual display + VNC server (unix socket, optional TCP)
                              ├─ adb server       owns the USB connection
                              ├─ run-scrcpy.sh    waits for the phone, sizes the display, runs scrcpy
                              ├─ display_sync.py  resizes the display when the phone rotates
                              └─ server.py        web UI, login, API, WebSocket ↔ VNC bridge
```

See [MAINTAINING.md](MAINTAINING.md) for how updates and releases work.

## License

GPL-3.0, like the original project. scrcpy is Apache-2.0 (Genymobile); noVNC is MPL-2.0; TigerVNC is GPL-2.0.
