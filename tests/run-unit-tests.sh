#!/bin/sh
# Run the Python test suite inside the image, against the exact code and
# library versions that ship. Usage: tests/run-unit-tests.sh [image]
set -eu
IMAGE="${1:-scrcpy-web:test}"
cd "$(dirname "$0")"
exec docker run --rm \
  --entrypoint python3 \
  -v "$PWD:/tests:ro" \
  -e SCRCPY_WEB_APP_DIR=/opt/scrcpy-web/app \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" -m unittest discover -s /tests -v
