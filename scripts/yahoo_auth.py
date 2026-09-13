"""Authorize with Yahoo over a local HTTPS listener.

Yahoo rejects a plain http://localhost redirect, so this serves HTTPS with a self-signed
certificate. Your browser will warn about it; that is expected. The `oob` copy-paste flow is
still documented but the registration form wants a real redirect URI, so this is the path
that keeps working.
"""

from __future__ import annotations

import json
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from ff.core.config import settings  # noqa: E402

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
code_holder: dict[str, str] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            code_holder["code"] = params["code"][0]
            body = b"Authorized. You can close this tab."
        else:
            body = b"No code in the callback. Check the redirect URI on your Yahoo app."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


def self_signed_cert() -> tuple[str, str]:
    tmp = Path(tempfile.mkdtemp())
    cert, key = tmp / "cert.pem", tmp / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


def main() -> int:
    cfg = settings()
    if not cfg.yahoo_client_id or not cfg.yahoo_client_secret:
        print("Set FF_YAHOO_CLIENT_ID and FF_YAHOO_CLIENT_SECRET in .env first.")
        print("Walkthrough: docs/YAHOO_SETUP.md")
        return 2

    # fspt-w requests write. Note this is not enough on its own: Read/Write must ALSO be
    # enabled on the app in YDN, or you get a read-only token with no error.
    params = urllib.parse.urlencode(
        {
            "client_id": cfg.yahoo_client_id,
            "redirect_uri": cfg.yahoo_redirect_uri,
            "response_type": "code",
            "scope": "fspt-w",
        }
    )
    url = f"{AUTH_URL}?{params}"
    print("Opening Yahoo consent page. Accept the self-signed certificate warning.\n")
    print(url, "\n")
    webbrowser.open(url)

    cert, key = self_signed_cert()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    port = urllib.parse.urlparse(cfg.yahoo_redirect_uri).port or 8080
    server = HTTPServer(("localhost", port), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    while "code" not in code_holder:
        server.handle_request()

    import httpx

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code_holder["code"],
            "redirect_uri": cfg.yahoo_redirect_uri,  # required, and must match exactly
            "client_id": cfg.yahoo_client_id,
            "client_secret": cfg.yahoo_client_secret,
        },
        auth=(cfg.yahoo_client_id, cfg.yahoo_client_secret),
        timeout=30.0,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed: HTTP {resp.status_code}")
        print(resp.text[:400])
        return 1

    token = resp.json()
    token.update(
        {"consumer_key": cfg.yahoo_client_id, "consumer_secret": cfg.yahoo_client_secret}
    )
    path = Path(cfg.yahoo_token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2))
    path.chmod(0o600)
    print(f"Token saved to {path}. It expires in an hour and refreshes automatically.")
    print("Next: make leagues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
