#!/usr/bin/env python3
"""Keep the virtual display the same shape as the phone.

When the phone rotates, scrcpy resizes its window to the new aspect ratio.
This loop notices that, resizes the virtual X display to the rotated video
size and stretches the scrcpy window over it, so VNC/noVNC clients always see
the whole phone screen with no black bars, in portrait and landscape.

Disabled when VNC_SCREEN_SIZE fixes the display size (scrcpy then runs
fullscreen and letterboxes instead).
"""
import os
import subprocess
import sys
import time

RUN_DIR = os.environ.get("RUN_DIR", "/run/scrcpy-web")
BASE_FILE = os.path.join(RUN_DIR, "base-size")
SET_SIZE = "/opt/scrcpy-web/bin/set-display-size"
WINDOW_TITLE = "^scrcpy-web$"
TOLERANCE = 2  # px; scrcpy's aspect-ratio lock may round by a pixel


def log(msg):
    print(f"[display-sync] {msg}", flush=True)


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=5)


def read_base():
    try:
        with open(BASE_FILE) as f:
            w, h = (int(v) for v in f.read().split())
        return w, h
    except (OSError, ValueError):
        return None


def find_window():
    r = run("xdotool", "search", "--onlyvisible", "--name", WINDOW_TITLE)
    ids = r.stdout.split()
    return ids[0] if ids else None


def window_geometry(wid):
    r = run("xdotool", "getwindowgeometry", "--shell", wid)
    vals = dict(line.split("=", 1) for line in r.stdout.split() if "=" in line)
    try:
        return int(vals["X"]), int(vals["Y"]), int(vals["WIDTH"]), int(vals["HEIGHT"])
    except (KeyError, ValueError):
        return None


def display_size():
    r = run("xrandr")
    for line in r.stdout.splitlines():
        if "current" in line:
            # Screen 0: minimum 32 x 32, current 720 x 1280, maximum ...
            part = line.split("current", 1)[1].split(",", 1)[0]
            w, h = part.split("x")
            return int(w), int(h)
    return None


def target_for(base, landscape):
    long_side, short_side = max(base), min(base)
    return (long_side, short_side) if landscape else (short_side, long_side)


def close(a, b):
    return abs(a[0] - b[0]) <= TOLERANCE and abs(a[1] - b[1]) <= TOLERANCE


def main():
    if os.environ.get("VNC_SCREEN_SIZE"):
        log("VNC_SCREEN_SIZE is set: fixed display size, rotation sync disabled")
        while True:
            time.sleep(3600)

    os.environ.setdefault("DISPLAY", ":1")
    last_target = None
    while True:
        time.sleep(0.25)
        try:
            base = read_base()
            wid = find_window() if base else None
            geo = window_geometry(wid) if wid else None
            if not geo:
                last_target = None
                continue
            x, y, w, h = geo
            target = target_for(base, landscape=w > h)

            current = display_size()
            if current and not close(current, target):
                subprocess.run([SET_SIZE, str(target[0]), str(target[1])], check=True, timeout=5)
            if not close((w, h), target) or (x, y) != (0, 0):
                run("xdotool", "windowsize", wid, str(target[0]), str(target[1]))
                run("xdotool", "windowmove", wid, "0", "0")
            if target != last_target:
                log(f"{'Landscape' if target[0] > target[1] else 'Portrait'}: display {target[0]}x{target[1]}")
                last_target = target
        except (subprocess.SubprocessError, OSError) as e:
            log(f"error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
