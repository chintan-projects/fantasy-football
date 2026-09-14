"""Per-source TTL cache. Never refetch faster than the upstream actually updates."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ff.core.logging import get_logger

log = get_logger(__name__)

#: Real upstream cadences, from docs/DATA_SOURCES.md. Refetching faster buys nothing and
#: spends throttle budget.
DEFAULT_TTL_SECONDS: dict[str, int] = {
    "yahoo_settings": 7 * 24 * 3600,  # league settings change ~never in a season
    "yahoo_roster": 300,
    "yahoo_freeagents": 900,
    "sleeper_state": 3600,
    "sleeper_players": 24 * 3600,  # Sleeper asks for at most one fetch a day; 5 MB payload
    "sleeper_trending": 3600,
    # Rotowire revises through the week as news lands; an hour-stale projection on a
    # Sunday morning is a wrong recommendation, not a slightly old one.
    "sleeper_projections": 1800,
    "espn_projections": 3600,
    # Short on purpose. The owner bids from the Yahoo app, so a balance more than a few
    # minutes old may already be wrong, and it is re-read before every submission anyway.
    "yahoo_faab": 120,
    # Completed transactions never change once written.
    "yahoo_transactions": 6 * 3600,
    "espn_scoreboard": 900,
    "nflverse_injuries": 6 * 3600,
    # A finished week's points never change. The long TTL is for the current week, whose
    # rows arrive through Monday night and are worth re-reading a few times a day.
    "nflverse_actuals": 6 * 3600,
    "nflverse_snaps": 6 * 3600,
    "fantasypros_ecr": 6 * 3600,
    "weather": 3 * 3600,
}


class FileCache:
    """A directory of JSON blobs. Boring on purpose; the dataset is a few MB a season."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace(":", "_")
        return self.root / f"{safe}.json"

    def get(self, key: str, ttl_s: int | None = None) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        ttl = ttl_s if ttl_s is not None else DEFAULT_TTL_SECONDS.get(key.split(":")[0], 3600)
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("cache_corrupt", key=key)
            return None

    def set(self, key: str, value: Any) -> None:
        self._path(key).write_text(json.dumps(value))

    def age_seconds(self, key: str) -> float | None:
        path = self._path(key)
        return None if not path.exists() else time.time() - path.stat().st_mtime
