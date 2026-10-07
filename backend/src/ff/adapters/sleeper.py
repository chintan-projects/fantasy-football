"""Sleeper. Free, documented, versioned, and the clearest terms of any source here.

Used for four things: the canonical current week, the trending-add demand signal, weekly
projections, and -- in manual league mode -- the player directory that names typed in from a
screenshot are matched against. Stay under 1000 calls/minute. The full player list is ~5 MB and must
be fetched at most once a day -- Sleeper asks for this explicitly.

Two hosts, and the difference matters. The documented API is ``api.sleeper.app/v1``. The
projections endpoint lives on ``api.sleeper.com`` with no version prefix and no entry in
the public docs, so it is treated like ESPN: validated on every fetch, never trusted to
keep its shape. Verified live on 2026-09-13 -- see ``SleeperProjections``.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ff.adapters._common import Scoring, canonical_team, match_key, parse_position
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, Position, SourceStatus, slots_for

BASE = "https://api.sleeper.app/v1"

#: The projections endpoint. Different host, no version prefix, undocumented.
PROJECTIONS_BASE = "https://api.sleeper.com"

#: Which scored total to read. Sleeper publishes all three, so no conversion is needed;
#: the league's format comes from ``FF_SCORING``.
SCORING_KEYS: dict[str, str] = {
    "std": "pts_std",
    "half_ppr": "pts_half_ppr",
    "ppr": "pts_ppr",
}


#: Ask only for what a fantasy roster can hold. The unfiltered pull returns punters and
#: cornerbacks -- 3304 rows against 470 that carry a scored projection.
PROJECTION_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

log = get_logger(__name__)


class SleeperClient:
    def __init__(self, cache: FileCache, client: httpx.Client | None = None) -> None:
        self.cache = cache
        self.client = client or httpx.Client(timeout=15.0)
        self._players: tuple[float, list[Player]] | None = None

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

    def players(self) -> list[Player]:
        """Every player at a position fantasy rosters hold, keyed by Sleeper's own id.

        This is the full ~5 MB list, so it is cached for a day on disk (Sleeper asks for at
        most one fetch a day). The parsed list is also held in memory for a day, because
        re-reading and re-parsing 5 MB on every tool call is the slow part.
        """
        ttl = DEFAULT_TTL_SECONDS["sleeper_players"]
        now = time.monotonic()
        if self._players is not None and now - self._players[0] < ttl:
            return self._players[1]
        data = self._get("/players/nfl", "sleeper_players:nfl")
        if not isinstance(data, dict) or not data:
            raise SchemaDrift("sleeper", "players payload is not a non-empty object", required=True)
        self._players = (now, parse_players(data))
        return self._players[1]

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


def parse_players(payload: dict[str, Any]) -> list[Player]:
    """Sleeper's player map, reduced to players a fantasy roster can hold.

    Linebackers, guards and punters are dropped: a fantasy roster cannot hold one, and
    keeping them would make "Josh Allen" ambiguous between the quarterback and a guard.
    Players with no NFL team are kept -- a free agent can still sit on a fantasy bench --
    and the matcher prefers rostered players when a name is shared.

    Team defenses have no ``full_name``; their name is city plus nickname, "Buffalo Bills",
    which is what Yahoo calls them too.
    """
    out: list[Player] = []
    for player_id, row in payload.items():
        if not isinstance(row, dict):
            continue
        position = parse_position(row.get("position"))
        if position is None:
            continue
        first = str(row.get("first_name") or "").strip()
        last = str(row.get("last_name") or "").strip()
        name = str(row.get("full_name") or f"{first} {last}").strip()
        if not name:
            continue
        out.append(
            Player(
                id=PlayerId(str(player_id)),
                name=name,
                position=position,
                team=canonical_team(row.get("team")),
                eligible_slots=slots_for(position),
                injury_status=row.get("injury_status") or None,
            )
        )
    return out
