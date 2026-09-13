"""Sleeper. Free, documented, versioned, and the clearest terms of any source here.

Used for three things: the canonical current week, the trending-add demand signal, and
weekly projections. Stay under 1000 calls/minute. The full player list is ~5 MB and must
be fetched at most once a day -- Sleeper asks for this explicitly.

Two hosts, and the difference matters. The documented API is ``api.sleeper.app/v1``. The
projections endpoint lives on ``api.sleeper.com`` with no version prefix and no entry in
the public docs, so it is treated like ESPN: validated on every fetch, never trusted to
keep its shape. Verified live on 2026-09-13 -- see ``SleeperProjections``.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from ff.adapters._common import canonical_team, match_key, parse_position
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, Position, SourceStatus

BASE = "https://api.sleeper.app/v1"

#: The projections endpoint. Different host, no version prefix, undocumented.
PROJECTIONS_BASE = "https://api.sleeper.com"

#: Which scored total to read. A league's scoring settings decide this; until league
#: settings are wired through (M3) the caller passes it and PPR is the default.
SCORING_KEYS: dict[str, str] = {
    "std": "pts_std",
    "half_ppr": "pts_half_ppr",
    "ppr": "pts_ppr",
}

Scoring = Literal["std", "half_ppr", "ppr"]

#: Ask only for what a fantasy roster can hold. The unfiltered pull returns punters and
#: cornerbacks -- 3304 rows against 470 that carry a scored projection.
PROJECTION_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

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


class SleeperProjections:
    """A ``ProjectionSource`` backed by Sleeper's weekly projections.

    Sleeper serves Rotowire's numbers. ESPN serves its own. That independence is the whole
    point of the pair: an average of sources beat individual sources in 63% of
    head-to-head comparisons, and averaging two views of the same underlying forecast would
    buy none of that.

    Verified live on 2026-09-13 against season 2026: 470 of 3304 returned rows carry a
    scored projection, including all 32 team defenses, with no key and no cost.

    Marked required, unlike ESPN, because it is the more complete of the two -- ESPN's
    pull is capped and sorted by percent owned, which drops deep-bench players, while
    Sleeper returns every position group whole. If this source is down there is no
    ensemble left to fall back to.
    """

    name = "sleeper"
    required = True

    def __init__(
        self,
        season: int,
        cache: FileCache,
        *,
        scoring: Scoring = "ppr",
        client: httpx.Client | None = None,
    ) -> None:
        self.season = season
        self.cache = cache
        self.scoring = scoring
        self.client = client or httpx.Client(timeout=30.0)
        self._last_error: str | None = None

    @property
    def points_key(self) -> str:
        return SCORING_KEYS[self.scoring]

    # ---- fetch -------------------------------------------------------------------

    def _cache_key(self, week: int) -> str:
        return f"sleeper_projections:{self.season}:{week}:{self.scoring}"

    def _fetch(self, week: int) -> Any:
        key = self._cache_key(week)
        cached = self.cache.get(key, DEFAULT_TTL_SECONDS["sleeper_projections"])
        if cached is not None:
            return cached

        positions = "".join(f"&position[]={p}" for p in PROJECTION_POSITIONS)
        url = (
            f"{PROJECTIONS_BASE}/projections/nfl/{self.season}/{week}"
            f"?season_type=regular{positions}&order_by={self.points_key}"
        )
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as exc:
            raise SourceUnavailable("sleeper", str(exc), required=self.required) from exc

        # Status before parsing. Always. See CLAUDE.md 2.4.
        if resp.status_code != 200:
            raise SourceUnavailable(
                "sleeper", f"HTTP {resp.status_code}: {resp.text[:200]}", required=self.required
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SchemaDrift(
                "sleeper", "HTTP 200 but the body is not JSON", required=self.required
            ) from exc

        validate_projections(payload, self.points_key)
        self.cache.set(key, payload)
        return payload

    # ---- ProjectionSource --------------------------------------------------------

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        """Projected points for this week, keyed by the Yahoo player id we were given."""
        try:
            payload = self._fetch(week)
        except (SourceUnavailable, SchemaDrift) as exc:
            self._last_error = str(exc)
            log.warning("sleeper_projections_unavailable", detail=str(exc))
            raise

        by_key = index_projections(payload, self.points_key)
        out: dict[PlayerId, float] = {}
        for player in players:
            value = by_key.get(match_key(player.name, player.position, player.team))
            if value is not None:
                out[player.id] = value
        self._last_error = None
        log.info("sleeper_projections", week=week, asked=len(players), matched=len(out))
        return out

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            ok=self._last_error is None,
            required=self.required,
            age_seconds=self.cache.age_seconds(self._cache_key(0)),
            detail=self._last_error,
        )


def validate_projections(payload: Any, points_key: str) -> None:
    """Check the shape on every fetch, not at startup.

    The failure that matters is not an exception, it is a 200 whose shape drifted just
    enough that the parse yields nothing and the app quietly runs on one source. So this
    asserts a scored projection is actually present, not merely that the JSON parsed.
    """
    if not isinstance(payload, list):
        raise SchemaDrift(
            "sleeper", f"top level is {type(payload).__name__}, not a list", required=True
        )
    if not payload:
        raise SchemaDrift("sleeper", "the projections list is empty", required=True)

    for row in payload:
        if not isinstance(row, dict):
            continue
        player = row.get("player")
        if not isinstance(player, dict):
            raise SchemaDrift("sleeper", "a row carries no 'player' object", required=True)
        stats = row.get("stats")
        if not isinstance(stats, dict):
            raise SchemaDrift("sleeper", "a row carries no 'stats' object", required=True)
        if stats.get(points_key) is not None:
            return

    raise SchemaDrift(
        "sleeper",
        f"{len(payload)} rows returned and not one carries a '{points_key}' value",
        required=True,
    )


def index_projections(payload: Any, points_key: str) -> dict[str, float]:
    """Flatten the response to ``match_key -> projected points``.

    Rows without a scored value are skipped rather than counted as zero: Sleeper returns
    every player it knows, and a punter with no projection is not a player projected to
    score nothing.
    """
    out: dict[str, float] = {}
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict):
            continue
        player = row.get("player")
        stats = row.get("stats")
        if not isinstance(player, dict) or not isinstance(stats, dict):
            continue
        points = stats.get(points_key)
        if points is None:
            continue

        position = parse_position(player.get("position")) or parse_position(
            (player.get("fantasy_positions") or [None])[0]
        )
        if position is None:
            continue
        team = canonical_team(row.get("team") or player.get("team"))

        # A team defense is named by its nickname ("Ravens"), so match_key joins it on the
        # team abbreviation instead -- which is exactly what it does for every source.
        name = (
            f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            if position is not Position.DEF
            else str(player.get("last_name", ""))
        )
        try:
            out[match_key(name, position, team)] = float(points)
        except (TypeError, ValueError):
            raise SchemaDrift(
                "sleeper", f"'{points_key}' is {points!r}, not a number", required=True
            ) from None
    return out
