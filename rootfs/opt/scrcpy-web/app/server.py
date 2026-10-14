#!/usr/bin/env python3
"""scrcpy-web web server.

Serves the web UI (noVNC), proxies VNC over a WebSocket to the local Xvnc
unix socket, and exposes a small control API (wake, unlock, keys, text,
screenshot, status) that drives the phone over ADB.

Only depends on the Python standard library and aiohttp (python3-aiohttp).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

from aiohttp import WSMsgType, web

log = logging.getLogger("web")

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
SESSION_COOKIE = "scrcpy_web_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "scrcpy-web"

# Named keys accepted by POST /api/key -> Android key codes
KEYS = {
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "recents": "KEYCODE_APP_SWITCH",
    "power": "KEYCODE_POWER",
    "wakeup": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "volume_mute": "KEYCODE_VOLUME_MUTE",
    "menu": "KEYCODE_MENU",
    "enter": "KEYCODE_ENTER",
    "delete": "KEYCODE_DEL",
    "tab": "KEYCODE_TAB",
    "escape": "KEYCODE_ESCAPE",
    "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS",
    "camera": "KEYCODE_CAMERA",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _read(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value or None


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# Networks that can't be reached from the internet. 100.64.0.0/10 (Tailscale,
# but also ISP carrier-grade NAT) is deliberately not included; add it
# explicitly if you need it.
PRIVATE_NETWORKS = (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "fc00::/7", "fe80::/10",
)
LOOPBACK_NETWORKS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128")]

# Host-name suffixes that only resolve on a local network or a private VPN.
LOCAL_HOST_SUFFIXES = (
    ".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain",
    ".intranet", ".corp", ".private", ".ts.net",
)


def parse_networks(value: str | None) -> list | None:
    """ALLOWED_NETWORKS: "private" (default), "any", or a comma-separated list of
    CIDRs/IPs, which may include the word "private"."""
    tokens = [t.strip().lower() for t in (value or "private").split(",") if t.strip()]
    if "any" in tokens:
        return None
    nets = []
    for t in tokens:
        if t == "private":
            nets += [ipaddress.ip_network(n) for n in PRIVATE_NETWORKS]
            continue
        try:
            nets.append(ipaddress.ip_network(t, strict=False))
        except ValueError:
            raise SystemExit(f"ALLOWED_NETWORKS: '{t}' is not a valid network (e.g. 192.168.1.0/24)")
    return nets


def parse_hosts(value: str | None) -> list | None:
    """ALLOWED_HOSTS: "auto" (default), "any", or a comma-separated list of host
    names (with optional "*." wildcards), which may include the word "auto"."""
    tokens = [t.strip().lower() for t in (value or "auto").split(",") if t.strip()]
    if "any" in tokens:
        return None
    return tokens


def ip_allowed(remote: str | None, networks: list | None) -> bool:
    if networks is None:
        return True
    if not remote:
        return False
    try:
        ip = ipaddress.ip_address(remote.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in n for n in LOOPBACK_NETWORKS):
        return True  # the container's own health check
    return any(ip.version == n.version and ip in n for n in networks)


def host_allowed(host_header: str | None, allowed: list | None) -> bool:
    if allowed is None:
        return True
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):  # [IPv6]:port
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.rstrip(".")
    for rule in allowed:
        if rule == "auto":
            try:
                ipaddress.ip_address(host)
                return True  # a bare IP can't be a rebinding attacker's domain
            except ValueError:
                pass
            if host == "localhost" or "." not in host or host.endswith(LOCAL_HOST_SUFFIXES):
                return True
        elif rule.startswith("*."):
            if host.endswith(rule[1:]):
                return True
        elif host == rule:
            return True
    return False


@dataclass
class Config:
    web_auth: str = "password"  # "password" or "none"
    web_password: str | None = None
    api_token: str | None = None
    unlock_pin: str | None = None
    vnc_password: str | None = None
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    session_days: int = 30
    wake_on_connect: bool = True
    allow_framing: bool = False
    # Who may connect: list of networks, or None for anyone (ALLOWED_NETWORKS=any)
    allowed_networks: list | None = field(default_factory=lambda: parse_networks("private"))
    # Host names the UI may be reached under (DNS-rebinding protection), or None for any
    allowed_hosts: list | None = field(default_factory=lambda: parse_hosts("auto"))
    serial: str | None = None
    adb_bin: str = "adb"
    host: str = "0.0.0.0"
    port: int = 8080
    tls_cert: str | None = None
    tls_key: str | None = None
    run_dir: Path = Path("/run/scrcpy-web")
    novnc_dir: Path = Path("/usr/share/novnc")
    supervisor_conf: str = "/etc/supervisor/scrcpy-web.conf"
    version: str = "dev"

    @property
    def vnc_socket(self) -> Path:
        return self.run_dir / "vnc.sock"

    @classmethod
    def from_env(cls) -> "Config":
        run_dir = Path(os.environ.get("RUN_DIR", "/run/scrcpy-web"))
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        cfg = cls(
            web_auth=os.environ.get("WEB_AUTH", "password").strip().lower(),
            web_password=_read(run_dir / "web-password"),
            api_token=_read(run_dir / "api-token"),
            unlock_pin=_read(run_dir / "unlock-pin"),
            vnc_password=_read(run_dir / "vnc-password"),
            session_days=int(os.environ.get("SESSION_DAYS", "30")),
            wake_on_connect=_env_bool("WAKE_ON_CONNECT", True),
            allow_framing=_env_bool("ALLOW_FRAMING", False),
            allowed_networks=parse_networks(os.environ.get("ALLOWED_NETWORKS", "private")),
            allowed_hosts=parse_hosts(os.environ.get("ALLOWED_HOSTS", "auto")),
            serial=os.environ.get("ADB_SERIAL") or None,
            adb_bin=os.environ.get("ADB_BIN", "adb"),
            host=os.environ.get("WEB_HOST", "0.0.0.0"),
            port=int(os.environ.get("WEB_PORT", "8080")),
            tls_cert=os.environ.get("TLS_CERT") or None,
            tls_key=os.environ.get("TLS_KEY") or None,
            run_dir=run_dir,
            novnc_dir=Path(os.environ.get("NOVNC_DIR", "/usr/share/novnc")),
            version=os.environ.get("SCRCPY_WEB_VERSION", "dev"),
        )
        secret = _read(data_dir / "session-secret")
        if secret:
            cfg.session_secret = secret
        if cfg.web_auth == "password" and not cfg.web_password:
            raise SystemExit("WEB_AUTH=password but no password is configured (did init.sh run?)")
        return cfg


# ---------------------------------------------------------------------------
# Device control over ADB
# ---------------------------------------------------------------------------
class AdbError(Exception):
    pass


def text_for_input(text: str) -> str:
    """Quote text for `adb shell input text`, which treats %s as a space and
    runs through the device shell."""
    return shlex.quote(text.replace(" ", "%s"))


PRINTABLE_ASCII = re.compile(r"^[\x20-\x7e]*$")


def parse_battery(out: str) -> dict:
    vals = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            vals[k.strip()] = v.strip()
    result: dict = {}
    if vals.get("level", "").isdigit():
        result["level"] = int(vals["level"])
    status = vals.get("status")
    if status:
        result["charging"] = status == "2"
        result["full"] = status == "5"
    result["plugged"] = any(vals.get(k) == "true" for k in ("AC powered", "USB powered", "Wireless powered"))
    temp = vals.get("temperature", "")
    if temp.lstrip("-").isdigit():
        result["temperature_c"] = int(temp) / 10
    return result


def parse_screen_on(out: str) -> bool | None:
    m = re.search(r"mWakefulness=(\w+)", out)
    if m:
        return m.group(1) == "Awake"
    m = re.search(r"Display Power: state=(\w+)", out)
    if m:
        return m.group(1) == "ON"
    return None


def parse_locked(out: str) -> bool | None:
    for pattern in (
        r"mShowingLockscreen=(true|false)",
        r"isStatusBarKeyguard=(true|false)",
        r"mDreamingLockscreen=(true|false)",
        r"\bshowing=(true|false)",
    ):
        m = re.search(pattern, out)
        if m:
            return m.group(1) == "true"
    return None


def parse_devices(out: str) -> list[dict]:
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        dev = {"serial": parts[0], "state": parts[1]}
        for p in parts[2:]:
            if ":" in p:
                k, v = p.split(":", 1)
                dev[k] = v
        devices.append(dev)
    return devices


class Device:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._status_cache: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    async def adb(self, *args: str, timeout: float = 10, serial: bool = True) -> bytes:
        cmd = [self.cfg.adb_bin]
        if serial and self.cfg.serial:
            cmd += ["-s", self.cfg.serial]
        cmd += list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as e:
            raise AdbError(f"cannot run adb: {e}") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise AdbError(f"adb {' '.join(args[:2])} timed out")
        if proc.returncode != 0:
            raise AdbError(err.decode(errors="replace").strip() or f"adb exited with {proc.returncode}")
        return out

    async def shell(self, command: str, timeout: float = 10) -> str:
        out = await self.adb("shell", command, timeout=timeout)
        return out.decode(errors="replace")

    async def devices(self) -> list[dict]:
        out = await self.adb("devices", "-l", serial=False, timeout=5)
        return parse_devices(out.decode(errors="replace"))

    async def connection(self) -> tuple[str, list[dict]]:
        """Returns (state, devices). state is one of connected, unauthorized,
        offline, disconnected, multiple."""
        devices = await self.devices()
        if self.cfg.serial:
            selected = [d for d in devices if d["serial"] == self.cfg.serial]
        else:
            if len(devices) > 1:
                return "multiple", devices
            selected = devices
        if not selected:
            return "disconnected", devices
        state = selected[0]["state"]
        return ("connected" if state == "device" else state), devices

    async def require_connected(self) -> None:
        state, _ = await self.connection()
        if state != "connected":
            raise AdbError(f"device is {state}")

    async def key(self, code: str) -> None:
        await self.shell(f"input keyevent {shlex.quote(code)}")

    async def wake(self) -> None:
        await self.key("KEYCODE_WAKEUP")
        self._status_cache = None

    async def sleep(self) -> None:
        await self.key("KEYCODE_SLEEP")
        self._status_cache = None

    async def text(self, text: str) -> None:
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                await self.shell(f"input text {text_for_input(line)}")
            if i < len(lines) - 1:
                await self.key("KEYCODE_ENTER")

    async def screen_size(self) -> tuple[int, int]:
        out = await self.shell("wm size")
        dims = re.findall(r"(\d+)x(\d+)", out)
        if not dims:
            raise AdbError("could not read screen size")
        w, h = (int(v) for v in dims[-1])
        return w, h

    async def is_locked(self) -> bool | None:
        out = await self.shell(
            "dumpsys window policy | grep -E 'mShowingLockscreen|isStatusBarKeyguard|mDreamingLockscreen|showing='"
        )
        return parse_locked(out)

    async def unlock(self, pin: str | None) -> dict:
        await self.wake()
        await asyncio.sleep(0.5)
        if await self.is_locked() is False:
            return {"locked": False, "already_unlocked": True}
        w, h = await self.screen_size()
        x = w // 2
        await self.shell(f"input swipe {x} {int(h * 0.8)} {x} {int(h * 0.25)} 250")
        await asyncio.sleep(0.8)
        if pin:
            await self.shell(f"input text {text_for_input(pin)}")
            await self.key("KEYCODE_ENTER")
            await asyncio.sleep(1.0)
        self._status_cache = None
        return {"locked": await self.is_locked()}

    async def screenshot(self) -> bytes:
        data = await self.adb("exec-out", "screencap", "-p", timeout=20)
        if not data.startswith(b"\x89PNG"):
            raise AdbError("screenshot failed")
        return data

    async def status(self) -> dict:
        async with self._lock:
            now = time.monotonic()
            if self._status_cache and now - self._status_cache[0] < 3:
                return self._status_cache[1]
            result = await self._status()
            self._status_cache = (now, result)
            return result

    async def _status(self) -> dict:
        try:
            state, devices = await self.connection()
        except AdbError as e:
            return {"state": "error", "error": str(e), "devices": []}
        info: dict = {
            "state": state,
            "serial": self.cfg.serial,
            "devices": [{"serial": d["serial"], "state": d["state"], "model": d.get("model")} for d in devices],
        }
        if state != "connected":
            return info
        cmd = (
            "echo @@props; getprop ro.product.manufacturer; getprop ro.product.model; "
            "getprop ro.build.version.release; getprop ro.build.version.sdk; "
            "echo @@battery; dumpsys battery; "
            "echo @@power; dumpsys power | grep -E 'mWakefulness=|Display Power'; "
            "echo @@window; dumpsys window policy | "
            "grep -E 'mShowingLockscreen|isStatusBarKeyguard|mDreamingLockscreen|showing='"
        )
        try:
            out = await self.shell(cmd, timeout=10)
        except AdbError as e:
            info["error"] = str(e)
            return info
        sections: dict[str, str] = {}
        current = None
        for line in out.splitlines():
            if line.startswith("@@"):
                current = line[2:].strip()
                sections[current] = ""
            elif current:
                sections[current] += line + "\n"
        props = [p.strip() for p in sections.get("props", "").splitlines()]
        props += [""] * (4 - len(props))
        info.update(
            manufacturer=props[0] or None,
            model=props[1] or None,
            android=props[2] or None,
            sdk=int(props[3]) if props[3].isdigit() else None,
            battery=parse_battery(sections.get("battery", "")),
            screen_on=parse_screen_on(sections.get("power", "")),
            locked=parse_locked(sections.get("window", "")),
        )
        return info


def scrcpy_running() -> bool:
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if Path(f"/proc/{pid}/comm").read_text().strip() == "scrcpy":
                    return True
            except OSError:
                continue
    return False


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class Auth:
    MAX_FREE_ATTEMPTS = 5

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._failures: dict[str, tuple[int, float]] = {}
        pw_fingerprint = hashlib.sha256((cfg.web_password or "").encode()).hexdigest()[:16]
        # Changing the password invalidates existing sessions.
        self._key = hashlib.sha256(f"{cfg.session_secret}:{pw_fingerprint}".encode()).digest()

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        expires = int(time.time()) + self.cfg.session_days * 86400
        payload = f"{expires}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def valid_session(self, token: str | None) -> bool:
        if not token or token.count(".") != 2:
            return False
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return False
        try:
            return int(payload.split(".", 1)[0]) > time.time()
        except ValueError:
            return False

    def valid_token(self, header: str | None) -> bool:
        if not self.cfg.api_token or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), self.cfg.api_token.encode())

    def locked_out(self, ip: str) -> float:
        count, until = self._failures.get(ip, (0, 0.0))
        return max(0.0, until - time.time())

    def record_failure(self, ip: str) -> None:
        """Counts a wrong password or API token. After MAX_FREE_ATTEMPTS the IP
        is locked out for 30 s, doubling with each further failure (max 1 h)."""
        now = time.time()
        if len(self._failures) > 10000:  # bound memory: forget stale entries
            self._failures = {k: v for k, v in self._failures.items() if v[1] > now - 3600}
        count, _ = self._failures.get(ip, (0, 0.0))
        count += 1
        delay = 0.0 if count < self.MAX_FREE_ATTEMPTS else min(3600.0, 30.0 * 2 ** (count - self.MAX_FREE_ATTEMPTS))
        self._failures[ip] = (count, now + delay)

    def check_password(self, ip: str, password: str) -> bool:
        ok = bool(self.cfg.web_password) and hmac.compare_digest(
            password.encode(), (self.cfg.web_password or "").encode()
        )
        if ok:
            self._failures.pop(ip, None)
            return True
        self.record_failure(ip)
        return False

    def request_auth(self, request: web.Request) -> str | None:
        """Returns how the request is authenticated (none/session/token) or None."""
        if self.cfg.web_auth == "none":
            return "none"
        if self.valid_session(request.cookies.get(SESSION_COOKIE)):
            return "session"
        if self.valid_token(request.headers.get("Authorization")):
            return "token"
        return None


# Typed application keys
CFG = web.AppKey("cfg", Config)
AUTH = web.AppKey("auth", Auth)
DEVICE = web.AppKey("device", Device)
SCRCPY_VERSION = web.AppKey("scrcpy_version", str)
WARNED_IPS = web.AppKey("warned_ips", set)


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
PUBLIC_PATHS = {"/login", "/healthz", "/favicon.svg"}


def is_secure(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


def json_error(status: int, message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


@web.middleware
async def access_guard(request: web.Request, handler):
    """Only serve the local network, and only under local host names."""
    cfg: Config = request.app[CFG]
    if not ip_allowed(request.remote, cfg.allowed_networks):
        warned: set = request.app[WARNED_IPS]
        if request.remote not in warned and len(warned) < 1000:
            warned.add(request.remote)
            log.warning("refused connection from %s (not in ALLOWED_NETWORKS)", request.remote)
        return web.Response(status=403, text="Forbidden: this server only accepts connections from the local network.\n")
    if not host_allowed(request.host, cfg.allowed_hosts):
        return web.Response(
            status=403,
            text=f"Forbidden: unknown host name '{request.host}'. If this is how you reach the server, "
            "add it to ALLOWED_HOSTS (e.g. ALLOWED_HOSTS=auto,phone.example.com).\n",
        )
    return await handler(request)


@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as e:  # redirects etc. get the headers too
        resp = e
    cfg: Config = request.app[CFG]
    resp.headers["Server"] = "scrcpy-web"
    if request.secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    csp = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self' ws: wss:; worker-src 'self' blob:"
    )
    if not cfg.allow_framing:
        resp.headers.setdefault("X-Frame-Options", "DENY")
        csp += "; frame-ancestors 'none'"
    resp.headers.setdefault("Content-Security-Policy", csp)
    if isinstance(resp, web.HTTPException):
        raise resp
    return resp


@web.middleware
async def require_auth(request: web.Request, handler):
    path = request.path
    auth: Auth = request.app[AUTH]
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await handler(request)
    method = auth.request_auth(request)
    if method is None and request.headers.get("Authorization") and auth.cfg.web_auth != "none":
        # wrong API token: rate limited exactly like wrong passwords
        ip = request.remote or "?"
        if auth.locked_out(ip) > 0:
            return json_error(429, "too many failed attempts, try again later")
        auth.record_failure(ip)
        log.warning("invalid API token from %s", ip)
        await asyncio.sleep(1)
    elif method == "token" and auth.locked_out(request.remote or "?") > 0:
        return json_error(429, "too many failed attempts, try again later")
    if method is None:
        if path.startswith("/api/") or path == "/websockify":
            return json_error(401, "authentication required")
        raise web.HTTPFound(f"/login?next={quote(request.path_qs, safe='')}")
    # CSRF: browser sessions must send a custom header on state-changing calls
    # (a cross-site form or fetch can't add it without a CORS preflight).
    if method == "session" and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE and path != "/logout":
            return json_error(403, f"missing {CSRF_HEADER} header")
    request["auth"] = method
    return await handler(request)


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>scrcpy-web · sign in</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/app.css">
</head><body class="login-body">
<form class="login-card" method="post" action="/login">
  <img src="/favicon.svg" alt="" width="44" height="44">
  <h1>scrcpy-web</h1>
  <p class="muted">Sign in to control your phone.</p>
  {error}
  <input type="hidden" name="next" value="{next}">
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
  <button type="submit" class="primary">Sign in</button>
</form>
</body></html>"""


LOGIN_ERRORS = {
    "wrong": "Wrong password",
    "locked": "Too many attempts. Wait a while and try again.",
}


def safe_next(value: str | None) -> str:
    """Only allow a local path to redirect to after login, never another host."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


async def login_page(request: web.Request) -> web.Response:
    cfg: Config = request.app[CFG]
    if cfg.web_auth == "none":
        raise web.HTTPFound("/")
    # only fixed messages: the URL can't put arbitrary text on the login page
    msg = LOGIN_ERRORS.get(request.query.get("error", ""))
    error = f'<p class="error" role="alert">{html.escape(msg)}</p>' if msg else ""
    body = LOGIN_PAGE.replace("{error}", error).replace(
        "{next}", html.escape(safe_next(request.query.get("next")), quote=True)
    )
    return web.Response(text=body, content_type="text/html", headers={"Cache-Control": "no-store"})


async def login_submit(request: web.Request) -> web.Response:
    auth: Auth = request.app[AUTH]
    form = await request.post()
    nxt = safe_next(str(form.get("next", "/")))
    ip = request.remote or "?"
    wait = auth.locked_out(ip)
    if wait > 0:
        raise web.HTTPFound(f"/login?next={quote(nxt, safe='')}&error=locked")
    if not auth.check_password(ip, str(form.get("password", ""))):
        log.warning("failed login from %s", ip)
        await asyncio.sleep(1)
        raise web.HTTPFound(f"/login?next={quote(nxt, safe='')}&error=wrong")
    log.info("login from %s", ip)
    resp = web.HTTPFound(nxt)
    resp.set_cookie(
        SESSION_COOKIE,
        auth.new_session(),
        max_age=request.app[CFG].session_days * 86400,
        httponly=True,
        samesite="Strict",
        secure=is_secure(request),
        path="/",
    )
    raise resp


async def logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/login")
    resp.del_cookie(SESSION_COOKIE, path="/")
    raise resp


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


async def favicon(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "favicon.svg")


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def websockify(request: web.Request) -> web.StreamResponse:
    """Bridge a noVNC WebSocket to the Xvnc unix socket."""
    cfg: Config = request.app[CFG]
    origin = request.headers.get("Origin")
    allowed_hosts = {request.host, request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()}
    if origin and urlparse(origin).netloc not in allowed_hosts:
        return json_error(403, "cross-origin WebSocket refused")

    ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=0, heartbeat=30)
    await ws.prepare(request)
    try:
        reader, writer = await asyncio.open_unix_connection(str(cfg.vnc_socket))
    except OSError as e:
        log.warning("VNC server not reachable: %s", e)
        await ws.close(code=1011, message=b"VNC server not running")
        return ws

    log.info("viewer connected from %s", request.remote)
    if cfg.wake_on_connect:
        asyncio.create_task(_quiet_wake(request.app[DEVICE]))

    async def vnc_to_ws():
        try:
            while data := await reader.read(65536):
                await ws.send_bytes(data)
        except (ConnectionError, RuntimeError):
            pass
        finally:
            await ws.close()

    pump = asyncio.create_task(vnc_to_ws())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type == WSMsgType.ERROR:
                break
    except ConnectionError:
        pass
    finally:
        pump.cancel()
        writer.close()
        log.info("viewer disconnected from %s", request.remote)
    return ws


async def _quiet_wake(device: Device) -> None:
    try:
        await device.wake()
    except AdbError:
        pass


# ----- API ------------------------------------------------------------------
def api(fn):
    """Decorator: turn AdbError into a 503 JSON response."""

    async def wrapper(request: web.Request):
        try:
            return await fn(request)
        except AdbError as e:
            return json_error(503, str(e))

    return wrapper


async def _json_body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text='{"ok":false,"error":"invalid JSON"}', content_type="application/json")
    return body if isinstance(body, dict) else {}


async def api_config(request: web.Request) -> web.Response:
    cfg: Config = request.app[CFG]
    return web.json_response(
        {
            "version": cfg.version,
            "scrcpy_version": request.app.get(SCRCPY_VERSION),
            "auth": cfg.web_auth,
            "vnc_password": cfg.vnc_password,
            "wake_on_connect": cfg.wake_on_connect,
            "unlock_pin_configured": bool(cfg.unlock_pin),
            "keys": sorted(KEYS),
        },
        headers={"Cache-Control": "no-store"},
    )


@api
async def api_status(request: web.Request) -> web.Response:
    device: Device = request.app[DEVICE]
    status = await device.status()
    return web.json_response(
        {"ok": True, "device": status, "mirroring": scrcpy_running(), "version": request.app[CFG].version},
        headers={"Cache-Control": "no-store"},
    )


@api
async def api_wake(request: web.Request) -> web.Response:
    device: Device = request.app[DEVICE]
    await device.require_connected()
    await device.wake()
    return web.json_response({"ok": True})


@api
async def api_sleep(request: web.Request) -> web.Response:
    device: Device = request.app[DEVICE]
    await device.require_connected()
    await device.sleep()
    return web.json_response({"ok": True})


@api
async def api_unlock(request: web.Request) -> web.Response:
    cfg: Config = request.app[CFG]
    device: Device = request.app[DEVICE]
    body = await _json_body(request)
    pin = body.get("pin") or cfg.unlock_pin
    if pin is not None and (not isinstance(pin, str) or not PRINTABLE_ASCII.match(pin) or len(pin) > 64):
        return json_error(400, "pin must be printable ASCII")
    await device.require_connected()
    log.info("unlock requested by %s", request.remote)
    result = await device.unlock(pin)
    return web.json_response({"ok": True, **result})


@api
async def api_key(request: web.Request) -> web.Response:
    device: Device = request.app[DEVICE]
    body = await _json_body(request)
    name = str(body.get("key", request.match_info.get("key", ""))).lower()
    if name == "notifications":
        await device.require_connected()
        await device.shell("cmd statusbar expand-notifications")
        return web.json_response({"ok": True})
    code = KEYS.get(name)
    if not code:
        return json_error(400, f"unknown key '{name}'; valid keys: {', '.join(sorted(KEYS))}, notifications")
    await device.require_connected()
    await device.key(code)
    return web.json_response({"ok": True})


@api
async def api_text(request: web.Request) -> web.Response:
    device: Device = request.app[DEVICE]
    body = await _json_body(request)
    text = body.get("text")
    if not isinstance(text, str) or not text:
        return json_error(400, "text is required")
    if len(text) > 2000:
        return json_error(400, "text is too long (max 2000 characters)")
    if not all(PRINTABLE_ASCII.match(line) for line in text.split("\n")):
        return json_error(400, "only plain ASCII text can be typed through ADB")
    await device.require_connected()
    await device.text(text)
    return web.json_response({"ok": True})


@api
async def api_screenshot(request: web.Request) -> web.Response:
    device: Device = request.app[DEVICE]
    await device.require_connected()
    data = await device.screenshot()
    name = time.strftime("screenshot-%Y%m%d-%H%M%S.png")
    disposition = "attachment" if request.query.get("download") else "inline"
    return web.Response(
        body=data,
        content_type="image/png",
        headers={"Cache-Control": "no-store", "Content-Disposition": f'{disposition}; filename="{name}"'},
    )


async def api_restart(request: web.Request) -> web.Response:
    cfg: Config = request.app[CFG]
    proc = await asyncio.create_subprocess_exec(
        "supervisorctl", "-c", cfg.supervisor_conf, "restart", "scrcpy",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    ok = proc.returncode == 0
    return web.json_response({"ok": ok, "output": out.decode(errors="replace").strip()}, status=200 if ok else 500)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
async def _detect_scrcpy_version(app: web.Application) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "scrcpy", "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
        m = re.search(r"scrcpy\s+v?(\S+)", out.decode(errors="replace"))
        app[SCRCPY_VERSION] = m.group(1) if m else None
    except (OSError, asyncio.TimeoutError):
        app[SCRCPY_VERSION] = None


def create_app(cfg: Config) -> web.Application:
    app = web.Application(middlewares=[security_headers, access_guard, require_auth], client_max_size=64 * 1024)
    app[CFG] = cfg
    app[WARNED_IPS] = set()
    app[AUTH] = Auth(cfg)
    app[DEVICE] = Device(cfg)
    app.on_startup.append(_detect_scrcpy_version)

    r = app.router
    r.add_get("/", index)
    r.add_get("/login", login_page)
    r.add_post("/login", login_submit)
    r.add_post("/logout", logout)
    r.add_get("/healthz", healthz)
    r.add_get("/favicon.svg", favicon)
    r.add_get("/websockify", websockify)
    r.add_get("/api/config", api_config)
    r.add_get("/api/status", api_status)
    r.add_post("/api/wake", api_wake)
    r.add_post("/api/sleep", api_sleep)
    r.add_post("/api/unlock", api_unlock)
    r.add_post("/api/key", api_key)
    r.add_post("/api/key/{key}", api_key)
    r.add_post("/api/text", api_text)
    r.add_get("/api/screenshot", api_screenshot)
    r.add_post("/api/restart", api_restart)
    r.add_static("/static/", STATIC_DIR)
    if cfg.novnc_dir.is_dir():
        r.add_static("/novnc/", cfg.novnc_dir)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[web] %(message)s", stream=sys.stdout)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = Config.from_env()
    ssl_ctx = None
    if cfg.tls_cert or cfg.tls_key:
        if not (cfg.tls_cert and cfg.tls_key):
            raise SystemExit("Set both TLS_CERT and TLS_KEY to enable HTTPS")
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(cfg.tls_cert, cfg.tls_key)
    log.info("listening on %s://%s:%d", "https" if ssl_ctx else "http", cfg.host, cfg.port)
    if cfg.allowed_networks is None:
        log.warning("ALLOWED_NETWORKS=any: connections are accepted from ANY address")
    else:
        log.info("accepting connections from: %s", ", ".join(str(n) for n in cfg.allowed_networks) + " (+ loopback)")
    if cfg.allowed_hosts is None:
        log.warning("ALLOWED_HOSTS=any: DNS-rebinding protection is off")
    web.run_app(create_app(cfg), host=cfg.host, port=cfg.port, ssl_context=ssl_ctx, access_log=None, print=None)


if __name__ == "__main__":
    main()
