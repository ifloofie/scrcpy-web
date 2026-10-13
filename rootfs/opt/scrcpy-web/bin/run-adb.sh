#!/bin/bash
# ADB server in the foreground. It owns the USB connection; scrcpy and the web
# app talk to it. With ADB_EXPOSE=true it also accepts connections from other
# machines (e.g. scrcpy on your PC via ADB_SERVER_SOCKET=tcp:<server>:5037).
set -euo pipefail
LOG_NAME=adb
. /opt/scrcpy-web/bin/common.sh

args=(-P "$ADB_PORT")
if is_true "${ADB_EXPOSE:-false}"; then
  args=(-a "${args[@]}")
fi

log "Starting ADB server on port $ADB_PORT"
exec adb "${args[@]}" nodaemon server start
