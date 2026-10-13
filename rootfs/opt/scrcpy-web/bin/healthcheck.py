#!/usr/bin/env python3
"""Docker HEALTHCHECK: the web server must answer /healthz."""
import os
import ssl
import sys
import urllib.request

port = os.environ.get("WEB_PORT", "8080")
scheme = "https" if os.environ.get("TLS_CERT") else "http"
ctx = ssl._create_unverified_context() if scheme == "https" else None  # local self-check only

try:
    with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/healthz", timeout=4, context=ctx) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception as e:  # noqa: BLE001
    print(f"unhealthy: {e}")
    sys.exit(1)
