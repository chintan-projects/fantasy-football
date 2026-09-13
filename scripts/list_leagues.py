"""Print every Yahoo fantasy league on this account, with the keys config needs."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from ff.adapters.yahoo.auth import TokenStore, YahooAuth  # noqa: E402
from ff.adapters.yahoo.client import YahooClient  # noqa: E402
from ff.core.cache import FileCache  # noqa: E402
from ff.core.config import settings  # noqa: E402
from ff.core.errors import FFError  # noqa: E402


def main() -> int:
    cfg = settings()
    store = TokenStore(Path(cfg.yahoo_token_path))
    try:
        auth = YahooAuth(
            store,
            client_id=cfg.yahoo_client_id,
            client_secret=cfg.yahoo_client_secret,
            redirect_uri=cfg.yahoo_redirect_uri,
        )
        client = YahooClient(auth, FileCache(Path(".cache")))
        # is_available=1 keeps this to the current season's leagues.
        payload = client.get(
            "/users;use_login=1/games;game_codes=nfl;is_available=1/leagues",
            "yahoo_settings:my_leagues",
            ttl_s=0,
        )
    except FFError as exc:
        print(exc)
        return 1

    found = list(_walk_leagues(payload))
    if not found:
        print("No leagues found. Is this the right Yahoo account?")
        return 1

    print("Put these in your local settings file:\n")
    for key, name in found:
        league_id = key.split(".l.")[-1]
        print(f"  {name}")
        print(f"    FF_YAHOO_LEAGUE_KEY={league_id}")
    print(
        "\nThe bare league id is the better value: it carries no season, so it does not"
    )
    print(
        "go stale when Yahoo rolls the game id over. A full key like 470.l.1000 also works."
    )
    print(
        "\nYour team key looks like <game_id>.l.<league_id>.t.<n> -- it is in the URL of"
    )
    print("your team page.")
    return 0


def _walk_leagues(node: Any) -> Any:
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
