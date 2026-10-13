#!/bin/bash
# Virtual X display :1 with TigerVNC's built-in VNC server.
#  - The web UI always connects through a private unix socket.
#  - A TCP port for native VNC apps is opened only when VNC_ENABLE=true.
set -euo pipefail
LOG_NAME=xvnc
. /opt/scrcpy-web/bin/common.sh

# Fixed size if VNC_SCREEN_SIZE is set (e.g. 1080x1920), otherwise a
# placeholder that run-scrcpy.sh replaces with the phone's real shape.
geometry="${VNC_SCREEN_SIZE:-720x1280}"
if ! [[ "$geometry" =~ ^[0-9]+x[0-9]+$ ]]; then
  log "ERROR: VNC_SCREEN_SIZE must look like 1080x1920 (got '$geometry')"
  exit 1
fi

rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 "$RUN_DIR/vnc.sock"

args=(
  :1
  -geometry "$geometry"
  -depth 24
  -desktop scrcpy-web
  -nolisten tcp
  -rfbunixpath "$RUN_DIR/vnc.sock"
  -rfbunixmode 0600
  -AlwaysShared=1
  -AcceptSetDesktopSize=0
  -FrameRate="${VNC_FRAME_RATE:-60}"
)

if is_true "${VNC_ENABLE:-false}"; then
  args+=(-rfbport "${VNC_PORT:-5900}" -SecurityTypes VncAuth -PasswordFile "$RUN_DIR/vnc.passwd")
else
  args+=(-rfbport -1 -SecurityTypes None)
fi

log "Starting virtual display ${geometry}"
exec Xvnc "${args[@]}"
