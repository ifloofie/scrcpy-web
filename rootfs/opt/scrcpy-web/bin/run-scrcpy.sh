#!/bin/bash
# Runs scrcpy against the phone, forever:
#   wait for the X display -> wait for an authorized device -> size the
#   display to the phone -> run scrcpy -> on exit, back off and start again.
set -uo pipefail
LOG_NAME=scrcpy
. /opt/scrcpy-web/bin/common.sh

serial=()
[ -n "${ADB_SERIAL:-}" ] && serial=(-s "$ADB_SERIAL")

child=""
on_term() {
  if [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null
    wait "$child" 2>/dev/null
  fi
  rm -f "$RUN_DIR/base-size"
  exit 0
}
trap on_term TERM INT

# Interruptible sleep (so docker stop is instant)
nap() { sleep "$1" & child=$!; wait "$child"; child=""; }

last_msg=""
say_once() {
  if [ "$1" != "$last_msg" ]; then
    log "$1"
    last_msg="$1"
  fi
}

wait_for_display() {
  until xrandr >/dev/null 2>&1; do nap 0.5; done
}

# Returns when the selected device is connected and authorized.
wait_for_device() {
  local list count state line
  while true; do
    list="$(adb devices 2>/dev/null | tail -n +2 | awk 'NF>=2 {print $1, $2}')"
    if [ ${#serial[@]} -gt 0 ]; then
      line="$(printf '%s\n' "$list" | awk -v s="$ADB_SERIAL" '$1==s')"
      state="${line#* }"
      [ -z "$line" ] && state=""
    else
      count="$(printf '%s' "$list" | grep -c . || true)"
      if [ "$count" -gt 1 ]; then
        say_once "Several devices are connected: $(printf '%s' "$list" | awk '{print $1}' | paste -sd' ' -). Set ADB_SERIAL to choose one."
        nap 3; continue
      fi
      state="$(printf '%s' "$list" | awk '{print $2}')"
    fi

    case "$state" in
      device) return 0 ;;
      unauthorized) say_once "Device is not authorized yet: unlock the phone and accept 'Allow USB debugging' (tick 'Always allow')." ;;
      offline) say_once "Device is offline: try unplugging and replugging the USB cable." ;;
      "") say_once "Waiting for a device${ADB_SERIAL:+ ($ADB_SERIAL)}... (USB debugging must be enabled)" ;;
      *) say_once "Device state is '$state', waiting..." ;;
    esac
    nap 2
  done
}

# Prints "W H": the video size scrcpy will produce, in the phone's natural
# orientation. Mirrors the scrcpy server's rounding so the display matches.
video_size() {
  local dims w h m major minor
  dims="$(adb "${serial[@]}" shell wm size 2>/dev/null | grep -oE '[0-9]+x[0-9]+' | tail -n1)"
  [ -n "$dims" ] || return 1
  w="${dims%x*}"; h="${dims#*x}"
  w=$(( w & ~7 )); h=$(( h & ~7 ))
  m=$(( ${MAX_SIZE:-1280} & ~7 ))
  if [ "$m" -gt 0 ]; then
    if [ "$h" -ge "$w" ]; then major=$h; minor=$w; else major=$w; minor=$h; fi
    if [ "$major" -gt "$m" ]; then
      minor=$(( (minor * m / major + 4) & ~7 ))
      major=$m
      if [ "$h" -ge "$w" ]; then h=$major; w=$minor; else w=$major; h=$minor; fi
    fi
  fi
  echo "$w $h"
}

build_args() {
  args=(
    "${serial[@]}"
    --window-title=scrcpy-web
    --window-borderless
    --window-x=0 --window-y=0
    --render-driver=software
    --no-audio
    --no-mouse-hover
    --max-size="${MAX_SIZE:-1280}"
    --max-fps="${MAX_FPS:-30}"
    --video-bit-rate="${VIDEO_BIT_RATE:-8M}"
    --video-codec="${VIDEO_CODEC:-h264}"
  )
  is_true "${STAY_AWAKE:-true}" && args+=(--stay-awake)
  is_true "${SCREEN_OFF:-false}" && args+=(--turn-screen-off)
  [ -n "${VNC_SCREEN_SIZE:-}" ] && args+=(--fullscreen)
  if [ -n "${SCRCPY_ARGS:-}" ]; then
    # Word-split on purpose: SCRCPY_ARGS is a list of extra flags.
    # shellcheck disable=SC2206
    local extra=(${SCRCPY_ARGS})
    local a
    for a in "${extra[@]}"; do
      # audio can't be carried over VNC, so it is always disabled above
      [ "$a" = "--no-audio" ] && continue
      args+=("$a")
    done
  fi
}

failures=0
while true; do
  wait_for_display
  wait_for_device
  last_msg=""

  if [ -z "${VNC_SCREEN_SIZE:-}" ]; then
    if size="$(video_size)"; then
      read -r bw bh <<< "$size"
      echo "$bw $bh" > "$RUN_DIR/base-size"
      /opt/scrcpy-web/bin/set-display-size "$bw" "$bh" || log "Could not resize display to ${bw}x${bh}"
      log "Display sized to the phone: ${bw}x${bh}"
    else
      log "Could not read the phone's screen size, keeping the current display size"
    fi
  fi

  build_args
  log "Starting: scrcpy ${args[*]}"
  started=$(date +%s)
  scrcpy "${args[@]}" &
  child=$!
  wait "$child"
  code=$?
  child=""
  rm -f "$RUN_DIR/base-size"

  # Back off if scrcpy keeps failing straight away; reset after a good run.
  if [ $(( $(date +%s) - started )) -gt 30 ]; then failures=0; else failures=$(( failures + 1 )); fi
  delay=$(( failures < 6 ? 2 ** failures : 60 ))
  log "scrcpy exited (code $code), restarting in ${delay}s"
  nap "$delay"
done
