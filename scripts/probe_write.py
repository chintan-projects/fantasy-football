"""Do writes work? As of 2026-09-14 the answer is no, and this is how we would find out
that it had changed.

Yahoo's access page states the Fantasy Sports API is read-only and that "write access is
not available at this time" -- not by default, not pending review, not available. So this
script is no longer a gate on shipping anything; it is a monitor. Run it occasionally, and
if it ever succeeds, CLAUDE.md section 6 and docs/DECISIONS.md both need rewriting.

It performs the smallest possible reversible write -- a roster PUT that sets your lineup to
exactly what it already is -- and reports the answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from ff.adapters.yahoo.payloads import set_lineup_xml  # noqa: E402
from ff.core.config import settings  # noqa: E402

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
STATUS_FILE = Path(__file__).resolve().parents[1] / ".yahoo_write_status"


def main() -> int:
    cfg = settings()
    if not cfg.yahoo_team_key:
        print("No FF_YAHOO_TEAM_KEY. Run `make leagues` first.")
        return 2

    try:
        from yahoo_oauth import OAuth2  # type: ignore[import-untyped]
    except ImportError:
        print("yahoo-oauth is not installed. Run `make setup`.")
        return 2

    oauth = OAuth2(None, None, from_file=cfg.yahoo_token_path)
    if not oauth.token_is_valid():
        oauth.refresh_access_token()

    # Read the current roster so the write is a no-op.
    week_resp = oauth.session.get(
        f"{BASE}/team/{cfg.yahoo_team_key}/roster", params={"format": "json"}
    )
    # Status before parsing, always: a throttled Yahoo returns HTTP 999 with an HTML body.
    if week_resp.status_code == 999:
        _write("THROTTLED (999). Try again in a few minutes.")
        return 3
    if week_resp.status_code != 200:
        _write(f"READ FAILED: HTTP {week_resp.status_code}. Check the token and league key.")
        return 3

    print("Read OK. Attempting a no-op roster PUT...")
    xml = set_lineup_xml(1, [])  # empty player list: valid, changes nothing
    resp = oauth.session.put(
        f"{BASE}/team/{cfg.yahoo_team_key}/roster",
        data=xml,
        headers={"Content-Type": "application/xml"},
    )

    if resp.status_code == 200:
        _write("WRITE OK — the API accepted a write. Set FF_WRITE_EXECUTOR=yahoo when ready.")
        return 0
    if resp.status_code in (401, 403):
        _write(
            f"WRITE DENIED (HTTP {resp.status_code}). Most likely a read-only token. Check "
            f"that Read/Write is enabled on the app in YDN AND that the authorize URL "
            f"requested fspt-w. If both are right, your access application is not approved "
            f"yet. Use FF_WRITE_EXECUTOR=assisted meanwhile."
        )
        return 1
    if resp.status_code == 999:
        _write("THROTTLED (999). Try again in a few minutes.")
        return 3
    _write(f"UNEXPECTED: HTTP {resp.status_code}. Body starts: {resp.text[:200]!r}")
    return 1


def _write(message: str) -> None:
    print(message)
    STATUS_FILE.write_text(f"Yahoo write access: {message}\n")


if __name__ == "__main__":
    raise SystemExit(main())
