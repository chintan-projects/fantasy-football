"""Sleeper. Free, documented, versioned, and the clearest terms of any source here.

Used for two things: the canonical current week, and the trending-add demand signal.
Stay under 1000 calls/minute. The full player list is ~5 MB and must be fetched at most
once a day -- Sleeper asks for this explicitly.
"""

from __future__ import annotations

from typing import Any

import httpx

from ff.core.cache import FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger

BASE = "https://api.sleeper.app/v1"
log = get_logger(__name__)


class SleeperClient:
    def __init__(self, cache: FileCache, client: httpx.Client | None = None) -> None:
        self.cache = cache
        self.client = client or httpx.Client(timeout=15.0)

    def _get(self, path: str, cache_key: str) -> Any:
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            resp = self.client.get(f"{BASE}{path}")
        except httpx.HTTPError as exc:
            raise SourceUnavailable("sleeper", str(exc), required=False) from exc
        # Check the status before parsing. Always. See CLAUDE.md 2.4.
        if resp.status_code != 200:
            raise SourceUnavailable("sleeper", f"HTTP {resp.status_code}", required=False)
        data = resp.json()
        self.cache.set(cache_key, data)
        return data

    def current_week(self) -> int:
        """The week oracle. Never compute the week from a date."""
        data = self._get("/state/nfl", "sleeper_state:nfl")
        if not isinstance(data, dict) or "week" not in data:
            raise SchemaDrift("sleeper", "state payload has no 'week'", required=True)
        return int(data["week"])

    def trending_adds(self, hours: int = 24, limit: int = 25) -> dict[str, int]:
        """Raw add counts. Normalize by availability before using -- see domain.faab."""
        path = f"/players/nfl/trending/add?lookback_hours={hours}&limit={limit}"
        data = self._get(path, f"sleeper_trending:add:{hours}:{limit}")
        if not isinstance(data, list):
            raise SchemaDrift("sleeper", "trending payload is not a list", required=False)
        return {row["player_id"]: int(row["count"]) for row in data if "player_id" in row}
