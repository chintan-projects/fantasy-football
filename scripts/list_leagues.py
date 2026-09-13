"""Print every Yahoo fantasy league on this account, with the keys .env needs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from ff.core.config import settings  # noqa: E402

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"


def main() -> int:
    cfg = settings()
    try:
        from yahoo_oauth import OAuth2  # type: ignore[import-untyped]
    except ImportError:
        print("yahoo-oauth is not installed. Run `make setup`.")
        return 2

    oauth = OAuth2(None, None, from_file=cfg.yahoo_token_path)
    if not oauth.token_is_valid():
        oauth.refresh_access_token()

    # Resolve the current season's game id rather than hardcoding it. 461 was 2025.
    resp = oauth.session.get(
        f"{BASE}/users;use_login=1/games;game_codes=nfl;is_available=1/leagues",
        params={"format": "json"},
    )
    if resp.status_code != 200:  # status before parsing, always
        print(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return 1

    payload = resp.json()
    found = list(_walk_leagues(payload))
    if not found:
        print("No leagues found. Is this the right Yahoo account?")
        return 1

    print("Put these in .env:\n")
    for key, name in found:
        print(f"  {name}")
        print(f"    FF_YAHOO_LEAGUE_KEY={key}")
    print("\nYour team key looks like <league_key>.t.<n> — find it in the league's team page URL.")
    return 0


def _walk_leagues(node: object) -> object:
    """Yahoo's JSON is an XML transliteration: repeated elements become objects keyed by
    "0", "1", ... with a sibling "count". Recursive descent is less painful than walking it
    by hand. See .claude/skills/yahoo-fantasy-api."""
    if isinstance(node, dict):
        if "league_key" in node and "name" in node:
            yield node["league_key"], node["name"]
        for value in node.values():
            yield from _walk_leagues(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_leagues(value)


if __name__ == "__main__":
    raise SystemExit(main())
