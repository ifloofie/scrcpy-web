#!/usr/bin/env python3
"""A stand-in for `adb` used by the tests.

Behaviour comes from a JSON scenario file ($FAKE_ADB_SCENARIO):
  {
    "devices": [["SERIAL", "device", "MODEL"], ...],
    "shell": {"<substring of command>": "<output>", ...},
    "locked": true
  }
Every invocation is appended to $FAKE_ADB_LOG as one JSON array per line.
"""
import json
import os
import sys

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def main(argv):
    scenario = {}
    path = os.environ.get("FAKE_ADB_SCENARIO")
    if path and os.path.exists(path):
        with open(path) as f:
            scenario = json.load(f)
    log = os.environ.get("FAKE_ADB_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps(argv) + "\n")

    args = list(argv)
    if args[:1] == ["-s"]:
        args = args[2:]

    devices = scenario.get("devices", [])
    if args[:1] == ["devices"]:
        print("List of devices attached")
        for serial, state, model in devices:
            print(f"{serial}\t{state} usb:1-1 product:x model:{model} device:x transport_id:1")
        print()
        return 0

    if not any(d[1] == "device" for d in devices):
        print("adb: no devices/emulators found", file=sys.stderr)
        return 1

    if args[:3] == ["exec-out", "screencap", "-p"]:
        sys.stdout.buffer.write(PNG)
        return 0

    if args[:1] == ["shell"]:
        command = " ".join(args[1:])
        # unlock simulation: entering text + enter after a swipe unlocks
        if "dumpsys window policy" in command and "@@" not in command:
            locked = scenario.get("locked", False)
            print(f"    mShowingLockscreen={'true' if locked else 'false'} mShowingDream=false")
            return 0
        for needle, output in scenario.get("shell", {}).items():
            if needle in command:
                sys.stdout.write(output)
                return 0
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
