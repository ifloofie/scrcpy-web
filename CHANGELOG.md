# Changelog

## 2.0.0 (2026-10-13)

First release of this fork of [robotfishe/scrcpy-web](https://github.com/robotfishe/scrcpy-web), rebuilt from the ground up.

### Fixed
- **The image builds again.** scrcpy 4.0 and later need SDL3, which Ubuntu 24.04 doesn't have, so the original Dockerfile can no longer build. It also hid the failure: errors inside its `RUN` heredoc were ignored, producing an image without scrcpy. The image now uses Ubuntu 26.04 LTS, pins scrcpy to a release (5.0.1), verifies the server jar's checksum, and fails loudly.
- **No shared ADB key.** The original image baked an ADB private key into the published image, the same for everyone. Keys are now generated per installation and kept in `/data`.
- **The phone is found again after it reboots or is replugged.** A `/dev/bus/usb` mount plus a device cgroup rule replace `--device=/dev/bus`.
- **Every service is supervised** (supervisord): the display, VNC, ADB, scrcpy and the web server all restart if they exit. Startup waits for the display and an authorized device instead of racing.
- **Clean, fast shutdown** (tini as PID 1); `docker stop` no longer waits 10 s.
- **Restarting the container works.** Stale X lock files are removed.
- **VNC is no longer open without a password by default,** and the password is no longer passed on the command line.
- **Rotation.** The virtual display follows the phone between portrait and landscape.
- Removed leftovers: the unused fluxbox setup, the "Chrome window" comment, duplicate packages, the no-op export, and the build-time `adb start-server`.

### Added
- Web UI (noVNC) with password login, for phones and desktops, installable as a home-screen app.
- Buttons for wake, unlock (optional stored PIN), back/home/recents, power, volume, notifications, screenshots, and text (Type via ADB, or Paste via the clipboard for any text).
- Wake on connect.
- HTTP API with bearer tokens (`docs/api.md`).
- Optional native VNC (`VNC_PASSWORD`) and remote ADB server (`ADB_EXPOSE`).
- Optional HTTPS (`TLS_CERT` / `TLS_KEY`).
- Settings: `MAX_SIZE`, `MAX_FPS`, `VIDEO_BIT_RATE`, `VIDEO_CODEC`, `STAY_AWAKE`, `SCREEN_OFF`, `ADB_SERIAL`, and `*_FILE` variants for secrets.
- Health check, docker-compose file, `.env.example`, updated Unraid template.
- Tests: Python unit/API tests with a fake adb, and an end-to-end container smoke test.
- CI, multi-arch (amd64/arm64) publishing to GHCR, weekly security rebuilds, and an automatic scrcpy update workflow.
