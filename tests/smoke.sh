#!/bin/bash
# End-to-end smoke test of a built image, without a phone attached.
# Usage: tests/smoke.sh [image]
set -euo pipefail
IMAGE="${1:-scrcpy-web:test}"
NAME="scrcpy-web-smoke-$$"
PORT="${SMOKE_PORT:-18765}"
VNC_PORT="${SMOKE_VNC_PORT:-18766}"
PW="smoke-test-password"
B="http://127.0.0.1:$PORT"
JAR="$(mktemp)"
fail() { echo "FAIL: $*" >&2; docker logs "$NAME" 2>&1 | tail -50 >&2; exit 1; }
pass() { echo "ok - $*"; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm -f "$NAME-data" >/dev/null 2>&1 || true; rm -f "$JAR"; }
trap cleanup EXIT

wait_healthy() {
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = healthy ] && return 0
    sleep 1
  done
  fail "container did not become healthy"
}

# The image must not contain an ADB key (each install generates its own)
if docker run --rm --entrypoint sh "$IMAGE" -c 'ls /root/.android/adbkey' >/dev/null 2>&1; then
  fail "image contains a baked-in ADB key"
fi
pass "no ADB key baked into the image"

docker run --rm --entrypoint scrcpy "$IMAGE" --version | head -n1 | grep -q '^scrcpy ' || fail "scrcpy missing"
pass "scrcpy is installed: $(docker run --rm --entrypoint scrcpy "$IMAGE" --version | head -n1)"

docker run -d --name "$NAME" \
  -e WEB_PASSWORD="$PW" -e VNC_PASSWORD=smokevnc \
  -p "127.0.0.1:$PORT:8080" -p "127.0.0.1:$VNC_PORT:5900" \
  -v "$NAME-data:/data" \
  --cap-drop ALL --security-opt no-new-privileges \
  "$IMAGE" >/dev/null
wait_healthy
pass "container healthy with all capabilities dropped"

status="$(docker exec "$NAME" supervisorctl -c /etc/supervisor/scrcpy-web.conf status)"
for svc in xvnc adb scrcpy display-sync web; do
  echo "$status" | grep -qE "^$svc +RUNNING" || fail "$svc is not running: $status"
done
pass "all services running"

[ "$(curl -s -o /dev/null -w '%{http_code}' "$B/healthz")" = 200 ] || fail "/healthz"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$B/api/status")" = 401 ] || fail "API must require auth"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$B/")" = 302 ] || fail "UI must redirect to login"
pass "unauthenticated requests are refused"

curl -s -c "$JAR" -o /dev/null -d "password=$PW&next=/" "$B/login"
curl -s -b "$JAR" "$B/api/status" | grep -q '"state": "disconnected"' || fail "status after login"
curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$B/novnc/core/rfb.js" | grep -q 200 || fail "noVNC not served"
curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$B/" | grep -q 200 || fail "UI after login"
pass "login, status API and UI assets work"

docker exec "$NAME" python3 - <<'EOF' || fail "WebSocket VNC bridge"
import asyncio, aiohttp
async def main():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        await s.post("http://127.0.0.1:8080/login", data={"password": "smoke-test-password", "next": "/"})
        async with s.ws_connect("http://127.0.0.1:8080/websockify", protocols=("binary",)) as ws:
            msg = await ws.receive(timeout=5)
            assert msg.data.startswith(b"RFB 003."), msg
asyncio.run(main())
EOF
pass "WebSocket reaches the VNC server"

python3 - "$VNC_PORT" <<'EOF' || fail "native VNC"
import socket, sys
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=5)
assert s.recv(12).startswith(b"RFB 003."), "no RFB banner"
s.sendall(b"RFB 003.008\n")
n = s.recv(1)[0]
assert list(s.recv(n)) == [2], "native VNC must require a password"
EOF
pass "native VNC requires a password"

fp1="$(docker exec "$NAME" sha256sum /data/adb/adbkey.pub | cut -d' ' -f1)"
docker restart -t 10 "$NAME" >/dev/null
wait_healthy
fp2="$(docker exec "$NAME" sha256sum /data/adb/adbkey.pub | cut -d' ' -f1)"
[ "$fp1" = "$fp2" ] || fail "ADB key changed across restart"
docker exec "$NAME" supervisorctl -c /etc/supervisor/scrcpy-web.conf status | grep -qE '^xvnc +RUNNING' || fail "Xvnc after restart (stale lock?)"
pass "restart keeps the ADB key and recovers the display"

start=$(date +%s)
docker stop -t 20 "$NAME" >/dev/null
elapsed=$(( $(date +%s) - start ))
[ "$elapsed" -lt 8 ] || fail "docker stop took ${elapsed}s (signals not handled)"
pass "clean shutdown in ${elapsed}s"

# A client outside ALLOWED_NETWORKS is refused, while the health check keeps working
docker rm -f "$NAME" >/dev/null
docker run -d --name "$NAME" -e WEB_PASSWORD="$PW" -e ALLOWED_NETWORKS=10.123.0.0/16 \
  -p "127.0.0.1:$PORT:8080" "$IMAGE" >/dev/null
wait_healthy
[ "$(curl -s -o /dev/null -w '%{http_code}' "$B/login")" = 403 ] || fail "client outside ALLOWED_NETWORKS was not refused"
docker logs "$NAME" 2>&1 | grep -q "not in ALLOWED_NETWORKS" || fail "refused connection not logged"
pass "clients outside ALLOWED_NETWORKS are refused"

docker rm -f "$NAME" >/dev/null
docker run -d --name "$NAME" -e WEB_PASSWORD="$PW" -p "127.0.0.1:$PORT:8080" "$IMAGE" >/dev/null
wait_healthy
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: rebind.attacker.example' "$B/login")" = 403 ] || fail "DNS-rebinding host name accepted"
pass "unknown host names are refused (DNS rebinding)"

echo "All smoke tests passed."
