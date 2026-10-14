"""Tests for the scrcpy-web web server, using a fake adb.

Run from the repo:      python3 -m unittest discover -s tests -v
Run inside the image:   see tests/run-unit-tests.sh
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = Path(os.environ.get("SCRCPY_WEB_APP_DIR", HERE.parent / "rootfs/opt/scrcpy-web/app"))
sys.path.insert(0, str(APP_DIR))

from aiohttp import WSMsgType  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402

import server  # noqa: E402

FAKE_ADB = str(HERE / "fake_adb.py")
CSRF = {"X-Requested-With": "scrcpy-web"}

STATUS_OUTPUT_ANDROID8 = """@@props
LGE
LG-H870
8.0.0
26
@@battery
Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  level: 87
  temperature: 291
@@power
  mWakefulness=Awake
@@window
    mShowingLockscreen=true mShowingDream=false
"""


class ParserTests(unittest.TestCase):
    def test_battery(self):
        b = server.parse_battery("  AC powered: false\n  USB powered: true\n  status: 2\n  level: 87\n  temperature: 291\n")
        self.assertEqual(b, {"level": 87, "charging": True, "full": False, "plugged": True, "temperature_c": 29.1})

    def test_screen_on(self):
        self.assertTrue(server.parse_screen_on("  mWakefulness=Awake\n"))
        self.assertFalse(server.parse_screen_on("  mWakefulness=Asleep\n"))
        self.assertFalse(server.parse_screen_on("  Display Power: state=OFF\n"))
        self.assertIsNone(server.parse_screen_on(""))

    def test_locked_variants(self):
        self.assertTrue(server.parse_locked("mShowingLockscreen=true mShowingDream=false"))  # Android 8
        self.assertFalse(server.parse_locked("isStatusBarKeyguard=false"))  # Android 10
        self.assertTrue(server.parse_locked("    KeyguardServiceDelegate\n      showing=true"))  # Android 12+
        self.assertIsNone(server.parse_locked("nothing here"))

    def test_devices(self):
        out = "List of devices attached\nABC123\tdevice usb:1-1 product:lucye model:LG_H870 device:lucye transport_id:2\nXYZ\tunauthorized usb:1-2 transport_id:3\n\n"
        devs = server.parse_devices(out)
        self.assertEqual([d["serial"] for d in devs], ["ABC123", "XYZ"])
        self.assertEqual(devs[0]["model"], "LG_H870")
        self.assertEqual(devs[1]["state"], "unauthorized")

    def test_text_quoting(self):
        self.assertEqual(server.text_for_input("hello world"), "hello%sworld")
        self.assertEqual(server.text_for_input("it's; rm -rf /"), "'it'\"'\"'s;%srm%s-rf%s/'")

    def test_safe_next(self):
        self.assertEqual(server.safe_next("/api/status"), "/api/status")
        self.assertEqual(server.safe_next("/?a=1"), "/?a=1")
        for bad in ("//evil.example/", "https://evil.example/", "/\\evil.example", "/\t/evil.example",
                    "/\n/evil.example", "evil.example", None, ""):
            self.assertEqual(server.safe_next(bad), "/", bad)


class AccessControlTests(unittest.TestCase):
    def test_default_networks_are_private_only(self):
        nets = server.parse_networks("private")
        for ip in ("192.168.1.20", "10.0.0.5", "172.20.1.1", "fd12::1", "fe80::1%eth0",
                   "::ffff:192.168.1.20", "127.0.0.1", "::1"):
            self.assertTrue(server.ip_allowed(ip, nets), ip)
        for ip in ("8.8.8.8", "41.0.0.1", "2001:4860::8888", "100.64.0.1", "::ffff:8.8.8.8", None, "garbage"):
            self.assertFalse(server.ip_allowed(ip, nets), ip)

    def test_custom_networks(self):
        nets = server.parse_networks("192.168.1.0/24, 100.64.0.0/10")
        self.assertTrue(server.ip_allowed("192.168.1.9", nets))
        self.assertTrue(server.ip_allowed("100.101.1.2", nets))
        self.assertFalse(server.ip_allowed("192.168.2.9", nets))
        self.assertTrue(server.ip_allowed("127.0.0.1", nets))  # health check always works
        self.assertIsNone(server.parse_networks("any"))
        self.assertTrue(server.ip_allowed("8.8.8.8", None))
        with self.assertRaises(SystemExit):
            server.parse_networks("not-a-network")

    def test_host_names(self):
        auto = server.parse_hosts("auto")
        for h in ("192.168.1.20:8080", "[fd00::1]:8080", "localhost:8080", "server", "server.lan",
                  "phone.local", "box.tail1234.ts.net", "nas.home.arpa"):
            self.assertTrue(server.host_allowed(h, auto), h)
        for h in ("evil.example", "192.168.1.20.nip.io", "attacker.com:8080", "", None):
            self.assertFalse(server.host_allowed(h, auto), h)
        custom = server.parse_hosts("auto, phone.example.com, *.mydomain.net")
        self.assertTrue(server.host_allowed("phone.example.com", custom))
        self.assertTrue(server.host_allowed("a.mydomain.net:443", custom))
        self.assertFalse(server.host_allowed("evil.example", custom))
        self.assertIsNone(server.parse_hosts("any"))


class AuthTests(unittest.TestCase):
    def make(self, **kw):
        return server.Auth(server.Config(web_password="pw", **kw))

    def test_session_roundtrip_and_tamper(self):
        auth = self.make(session_secret="s1")
        tok = auth.new_session()
        self.assertTrue(auth.valid_session(tok))
        self.assertFalse(auth.valid_session(tok[:-1] + ("0" if tok[-1] != "0" else "1")))
        self.assertFalse(auth.valid_session("garbage"))
        # different secret or password -> invalid
        self.assertFalse(self.make(session_secret="s2").valid_session(tok))
        self.assertFalse(server.Auth(server.Config(web_password="other", session_secret="s1")).valid_session(tok))

    def test_expired_session(self):
        auth = self.make(session_secret="s1")
        payload = f"{int(time.time()) - 10}.abcd"
        self.assertFalse(auth.valid_session(f"{payload}.{auth._sign(payload)}"))

    def test_lockout(self):
        auth = self.make()
        for _ in range(auth.MAX_FREE_ATTEMPTS - 1):
            self.assertFalse(auth.check_password("1.2.3.4", "nope"))
            self.assertEqual(auth.locked_out("1.2.3.4"), 0)
        self.assertFalse(auth.check_password("1.2.3.4", "nope"))
        self.assertGreater(auth.locked_out("1.2.3.4"), 0)
        self.assertEqual(auth.locked_out("5.6.7.8"), 0)

    def test_bearer_token(self):
        auth = self.make(api_token="tok123")
        self.assertTrue(auth.valid_token("Bearer tok123"))
        self.assertFalse(auth.valid_token("Bearer nope"))
        self.assertFalse(self.make().valid_token("Bearer tok123"))


class ServerTestCase(AioHTTPTestCase):
    scenario = {"devices": [["SER1", "device", "LG_H870"]], "shell": {"@@props": STATUS_OUTPUT_ANDROID8, "wm size": "Physical size: 1440x2880\n"}, "locked": True}
    config_overrides: dict = {}

    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.adb_log = tmp / "adb.log"
        scenario_file = tmp / "scenario.json"
        scenario_file.write_text(json.dumps(self.scenario))
        os.environ["FAKE_ADB_SCENARIO"] = str(scenario_file)
        os.environ["FAKE_ADB_LOG"] = str(self.adb_log)
        cfg = server.Config(
            web_password="secret-pw",
            api_token="api-token-1",
            adb_bin=FAKE_ADB,
            run_dir=tmp,
            novnc_dir=tmp / "missing",
            **self.config_overrides,
        )
        self.cfg = cfg
        return server.create_app(cfg)

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.tmp.cleanup()

    def adb_calls(self):
        if not self.adb_log.exists():
            return []
        return [json.loads(line) for line in self.adb_log.read_text().splitlines()]

    def shell_calls(self):
        return [c[c.index("shell") + 1] for c in self.adb_calls() if "shell" in c]

    async def login(self):
        r = await self.client.post("/login", data={"password": "secret-pw", "next": "/"}, allow_redirects=False)
        self.assertEqual(r.status, 302)
        return r


class HttpAuthTests(ServerTestCase):
    async def test_redirects_and_401(self):
        r = await self.client.get("/", allow_redirects=False)
        self.assertEqual(r.status, 302)
        self.assertTrue(r.headers["Location"].startswith("/login"))
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 401)
        r = await self.client.get("/healthz")
        self.assertEqual(r.status, 200)

    async def test_wrong_password(self):
        r = await self.client.post("/login", data={"password": "nope"}, allow_redirects=False)
        self.assertIn("error=", r.headers["Location"])
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 401)

    async def test_login_cookie_flags_and_open_redirect(self):
        r = await self.client.post("/login", data={"password": "secret-pw", "next": "//evil.example"}, allow_redirects=False)
        self.assertEqual(r.headers["Location"], "/")
        cookie = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    async def test_session_and_csrf(self):
        await self.login()
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 200)
        r = await self.client.post("/api/wake", json={})
        self.assertEqual(r.status, 403)  # no CSRF header
        r = await self.client.post("/api/wake", json={}, headers=CSRF)
        self.assertEqual(r.status, 200)

    async def test_bearer_token_needs_no_csrf(self):
        r = await self.client.post("/api/wake", headers={"Authorization": "Bearer api-token-1"})
        self.assertEqual(r.status, 200)
        r = await self.client.post("/api/wake", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status, 401)

    async def test_logout(self):
        await self.login()
        await self.client.post("/logout", allow_redirects=False)
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 401)

    async def test_rebinding_host_refused(self):
        r = await self.client.get("/login", headers={"Host": "attacker-rebind.example"})
        self.assertEqual(r.status, 403)
        r = await self.client.get("/login", headers={"Host": "192.168.1.20:8080"})
        self.assertEqual(r.status, 200)

    async def test_login_error_text_cannot_be_injected(self):
        r = await self.client.get("/login?error=Sign%20in%20at%20evil.example")
        self.assertNotIn("evil.example", await r.text())
        r = await self.client.get("/login?error=wrong")
        self.assertIn("Wrong password", await r.text())

    async def test_api_token_guessing_is_rate_limited(self):
        statuses = []
        for i in range(7):
            r = await self.client.get("/api/status", headers={"Authorization": f"Bearer guess{i}"})
            statuses.append(r.status)
        self.assertEqual(statuses[:5], [401] * 5)
        self.assertIn(429, statuses[5:])
        # even the right token is refused while locked out
        r = await self.client.get("/api/status", headers={"Authorization": "Bearer api-token-1"})
        self.assertEqual(r.status, 429)

    async def test_server_header_hides_versions(self):
        r = await self.client.get("/login")
        self.assertEqual(r.headers["Server"], "scrcpy-web")
        r = await self.client.get("/", allow_redirects=False)
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")  # headers on redirects too

    async def test_security_headers(self):
        r = await self.client.get("/login")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])


class NoAuthTests(ServerTestCase):
    config_overrides = {"web_auth": "none"}

    async def test_open_access(self):
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 200)
        r = await self.client.get("/login", allow_redirects=False)
        self.assertEqual(r.status, 302)


class ApiTests(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.login()

    async def test_status(self):
        r = await self.client.get("/api/status")
        data = await r.json()
        dev = data["device"]
        self.assertEqual(dev["state"], "connected")
        self.assertEqual(dev["model"], "LG-H870")
        self.assertEqual(dev["android"], "8.0.0")
        self.assertEqual(dev["sdk"], 26)
        self.assertEqual(dev["battery"]["level"], 87)
        self.assertTrue(dev["battery"]["charging"])
        self.assertTrue(dev["screen_on"])
        self.assertTrue(dev["locked"])

    async def test_wake_and_keys(self):
        await self.client.post("/api/wake", json={}, headers=CSRF)
        await self.client.post("/api/key", json={"key": "home"}, headers=CSRF)
        await self.client.post("/api/key/volume_up", headers=CSRF)
        self.assertEqual(self.shell_calls(), [
            "input keyevent KEYCODE_WAKEUP",
            "input keyevent KEYCODE_HOME",
            "input keyevent KEYCODE_VOLUME_UP",
        ])

    async def test_unknown_key(self):
        r = await self.client.post("/api/key", json={"key": "self_destruct"}, headers=CSRF)
        self.assertEqual(r.status, 400)
        self.assertEqual(self.shell_calls(), [])

    async def test_text_is_quoted_and_multiline(self):
        r = await self.client.post("/api/text", json={"text": "hi there; ls\nnext"}, headers=CSRF)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.shell_calls(), [
            "input text 'hi%sthere;%sls'",
            "input keyevent KEYCODE_ENTER",
            "input text next",
        ])

    async def test_text_rejects_non_ascii(self):
        r = await self.client.post("/api/text", json={"text": "héllo"}, headers=CSRF)
        self.assertEqual(r.status, 400)

    async def test_unlock_with_pin(self):
        r = await self.client.post("/api/unlock", json={"pin": "1234"}, headers=CSRF)
        self.assertEqual(r.status, 200)
        calls = self.shell_calls()
        self.assertEqual(calls[0], "input keyevent KEYCODE_WAKEUP")
        self.assertIn("input swipe 720 2304 720 720 250", calls)
        self.assertIn("input text 1234", calls)
        self.assertEqual(calls[calls.index("input text 1234") + 1], "input keyevent KEYCODE_ENTER")

    async def test_unlock_rejects_bad_pin(self):
        r = await self.client.post("/api/unlock", json={"pin": "12\n34"}, headers=CSRF)
        self.assertEqual(r.status, 400)

    async def test_screenshot(self):
        r = await self.client.get("/api/screenshot?download=1")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Content-Type"], "image/png")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertTrue((await r.read()).startswith(b"\x89PNG"))

    async def test_config_hides_nothing_sensitive(self):
        data = await (await self.client.get("/api/config")).json()
        self.assertNotIn("secret-pw", json.dumps(data))
        self.assertNotIn("api-token-1", json.dumps(data))
        self.assertFalse(data["unlock_pin_configured"])


class UnlockedPhoneTests(ServerTestCase):
    scenario = {**ServerTestCase.scenario, "locked": False}

    async def test_unlock_skips_swipe_when_unlocked(self):
        await self.login()
        r = await self.client.post("/api/unlock", json={}, headers=CSRF)
        self.assertTrue((await r.json())["already_unlocked"])
        self.assertFalse(any(c.startswith("input swipe") for c in self.shell_calls()))


class NoDeviceTests(ServerTestCase):
    scenario = {"devices": []}

    async def test_disconnected(self):
        await self.login()
        data = await (await self.client.get("/api/status")).json()
        self.assertEqual(data["device"]["state"], "disconnected")
        r = await self.client.post("/api/wake", json={}, headers=CSRF)
        self.assertEqual(r.status, 503)


class MultipleDevicesTests(ServerTestCase):
    scenario = {"devices": [["A1", "device", "One"], ["B2", "device", "Two"]], "shell": {}}

    async def test_multiple_needs_serial(self):
        await self.login()
        data = await (await self.client.get("/api/status")).json()
        self.assertEqual(data["device"]["state"], "multiple")
        self.assertEqual(len(data["device"]["devices"]), 2)


class SerialSelectionTests(ServerTestCase):
    scenario = {"devices": [["A1", "device", "One"], ["B2", "unauthorized", "Two"]], "shell": {}}
    config_overrides = {"serial": "B2"}

    async def test_selected_unauthorized(self):
        await self.login()
        data = await (await self.client.get("/api/status")).json()
        self.assertEqual(data["device"]["state"], "unauthorized")


class WebSocketProxyTests(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.received = bytearray()

        async def handle(reader, writer):
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            data = await reader.read(100)
            self.received.extend(data)
            writer.write(b"echo:" + data)
            await writer.drain()
            writer.close()

        self.vnc = await asyncio.start_unix_server(handle, path=str(self.cfg.vnc_socket))
        self.cfg.wake_on_connect = False

    async def asyncTearDown(self):
        self.vnc.close()
        await self.vnc.wait_closed()
        await super().asyncTearDown()

    async def test_requires_auth(self):
        r = await self.client.get("/websockify")
        self.assertEqual(r.status, 401)

    async def test_bridges_both_ways(self):
        await self.login()
        async with self.client.ws_connect("/websockify", protocols=("binary",)) as ws:
            msg = await ws.receive(timeout=5)
            self.assertEqual(msg.data, b"RFB 003.008\n")
            await ws.send_bytes(b"RFB 003.008\n")
            msg = await ws.receive(timeout=5)
            self.assertEqual(msg.data, b"echo:RFB 003.008\n")
            msg = await ws.receive(timeout=5)
            self.assertEqual(msg.type, WSMsgType.CLOSE)
        self.assertEqual(bytes(self.received), b"RFB 003.008\n")

    async def test_cross_origin_refused(self):
        await self.login()
        r = await self.client.get("/websockify", headers={
            "Origin": "https://evil.example", "Connection": "Upgrade", "Upgrade": "websocket",
            "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        })
        self.assertEqual(r.status, 403)

    async def test_origin_via_reverse_proxy(self):
        # proxies like nginx may rewrite Host but pass X-Forwarded-Host
        await self.login()
        async with self.client.ws_connect("/websockify", headers={
            "Origin": "https://phone.example.ts.net", "X-Forwarded-Host": "phone.example.ts.net",
        }) as ws:
            msg = await ws.receive(timeout=5)
            self.assertEqual(msg.data, b"RFB 003.008\n")


if __name__ == "__main__":
    unittest.main()
