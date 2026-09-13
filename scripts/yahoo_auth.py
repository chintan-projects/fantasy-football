"""Authorize with Yahoo over a local HTTPS listener, then prove the token works.

Yahoo rejects a plain http://localhost redirect, so this serves HTTPS with a self-signed
certificate. Your browser will warn about it; that is expected. The `oob` copy-paste flow is
still documented but the registration form wants a real redirect URI, so this is the path
that keeps working.

The last step is the point of the script: it makes one real read call. A token that saved
cleanly but cannot read is the failure mode worth catching here rather than on Sunday.
"""

from __future__ import annotations

import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from ff.adapters.yahoo.auth import (  # noqa: E402
    CallbackResult,
    TokenStore,
    YahooAuth,
    authorize_url,
    exchange_code,
    parse_callback,
)
from ff.adapters.yahoo.client import YahooClient  # noqa: E402
from ff.core.cache import FileCache  # noqa: E402
from ff.core.config import settings  # noqa: E402
from ff.core.errors import FFError  # noqa: E402

result_holder: dict[str, CallbackResult] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        result = parse_callback(urllib.parse.urlparse(self.path).query)
        if result.is_final:
            result_holder["result"] = result
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(result.message.encode("utf-8"))

    def log_message(self, *_: object) -> None:
        pass


def self_signed_cert() -> tuple[str, str]:
    tmp = Path(tempfile.mkdtemp())
    cert, key = tmp / "cert.pem", tmp / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


def wait_for_callback(redirect_uri: str) -> CallbackResult:
    """Serve until Yahoo says something definite -- a code or a refusal.

    Waiting only for a code meant a refusal hung here forever. (BUG-005.)
    """
    cert, key = self_signed_cert()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    port = urllib.parse.urlparse(redirect_uri).port or 8080
    server = HTTPServer(("localhost", port), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    while "result" not in result_holder:
        server.handle_request()
    return result_holder["result"]


def main() -> int:
    cfg = settings()
    if not cfg.yahoo_client_id or not cfg.yahoo_client_secret:
        print("Set FF_YAHOO_CLIENT_ID and FF_YAHOO_CLIENT_SECRET first.")
        print("Walkthrough: docs/YAHOO_SETUP.md")
        return 2

    # No scope parameter. Yahoo refuses fspt-r and fspt-w alike and reads permissions off
    # the app's own developer-portal settings instead -- see authorize_url for the probe.
    url = authorize_url(cfg.yahoo_client_id, cfg.yahoo_redirect_uri, scope=cfg.yahoo_scope)
    asked = cfg.yahoo_scope or "(none -- Yahoo uses the app's own permissions)"
    print(f"Requesting scope: {asked}")
    print("Opening Yahoo consent page. Accept the self-signed certificate warning.\n")
    print(url, "\n")
    webbrowser.open(url)

    result = wait_for_callback(cfg.yahoo_redirect_uri)
    if result.code is None:
        print(result.message)
        return 1
    code = result.code

    try:
        token = exchange_code(
            code,
            client_id=cfg.yahoo_client_id,
            client_secret=cfg.yahoo_client_secret,
            redirect_uri=cfg.yahoo_redirect_uri,
            now_epoch=time.time(),
        )
    except FFError as exc:
        print(f"Token exchange failed: {exc}")
        return 1

    store = TokenStore(Path(cfg.yahoo_token_path))
    store.save(token)
    print(f"Token saved to {store.path}, owner-readable only.")

    # Prove it. A token that saved cleanly but cannot read is the failure worth catching
    # now rather than at 11:58 on a Sunday.
    auth = YahooAuth(
        store,
        client_id=cfg.yahoo_client_id,
        client_secret=cfg.yahoo_client_secret,
        redirect_uri=cfg.yahoo_redirect_uri,
    )
    client = YahooClient(auth, FileCache(Path(".cache")))
    try:
        game_id = client.game_id()
    except FFError as exc:
        print(f"\nThe token saved but the first read failed: {exc}")
        return 1

    print(f"Read check passed. Current NFL game id is {game_id}.")
    print("It expires in an hour and refreshes itself at 55 minutes.")
    print("\nNext: make leagues")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
