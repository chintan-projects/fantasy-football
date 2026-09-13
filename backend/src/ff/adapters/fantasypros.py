"""FantasyPros: a second set of weekly projections, and the expert-disagreement number.

Two endpoints, two different jobs:

* ``/nfl/{season}/projections`` -- projected points. This is what makes FantasyPros a
  ``ProjectionSource`` and gives the ensemble its second member.
* ``/nfl/{season}/consensus-rankings`` -- ECR across 130+ experts, with ``rank_std``.
  This is the reason to pay the $8.99. It is the cheapest credible proxy anywhere for
  player-level *epistemic* uncertainty.

**The std-dev is in rank units, not points, and this module does not convert it.** Turning
a standard deviation of ranks into a standard deviation of points needs a rank-to-points
curve, which is decision math and belongs with the distribution work, not in an adapter.
``ranks()`` hands back the raw consensus so that work has something to start from.
``domain.blend`` still derives epistemic spread from cross-source disagreement. And per the
fantasy-decision-math skill: expert disagreement is not week-to-week volatility. They are
different quantities and substituting one for the other is the most common modelling error
in public fantasy tools.

**Response shapes here are unverified.** Every other adapter in this repo was probed
against a live endpoint. FantasyPros needs a paid key, there is none on this machine, and
a keyless call returns 403 ``{"message": "Forbidden"}`` -- which is the one thing here that
*is* recorded. So the parsers below name every field they require and raise ``SchemaDrift``
saying which field was missing, rather than tolerating an unexpected shape and quietly
returning an empty ensemble. Point a real key at it and the first fetch will say precisely
what is wrong, if anything is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ff.adapters._common import canonical_team, match_key, parse_position
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import ConfigError, SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, SourceStatus

BASE = "https://api.fantasypros.com/public/v2/json"

#: Field aliases exist only because the shape is unverified -- see the module docstring.
#: Each list is tried in order and a miss on all of them is a SchemaDrift naming them.
_NAME_FIELDS = ("player_name", "name")
_POSITION_FIELDS = ("player_position_id", "position_id", "position")
_TEAM_FIELDS = ("player_team_id", "team_id", "team")
_POINTS_FIELDS = ("points", "fpts", "proj_pts")

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RankConsensus:
    """One player's place in the expert consensus, in rank units.

    ``std_dev`` measures how much 130+ experts disagree about where he belongs. It does not
    measure how much his weekly output bounces around. See the module docstring.
    """

    ecr: float
    std_dev: float
    best: float
    worst: float
    tier: int | None = None


class FantasyProsProjections:
    """A ``ProjectionSource`` backed by FantasyPros. Required, because it is paid for."""

    name = "fantasypros"
    required = True

    def __init__(
        self,
        season: int,
        api_key: str,
        cache: FileCache,
        *,
        scoring: str = "PPR",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ConfigError(
                "FantasyPros needs FF_FANTASYPROS_API_KEY. The free tier returns sample "
                "data only; keys are at secure.fantasypros.com/api-keys/request. "
                "See docs/DATA_SOURCES.md."
            )
        self.season = season
        self.api_key = api_key
        self.cache = cache
        self.scoring = scoring
        self.client = client or httpx.Client(timeout=30.0)
        self._last_error: str | None = None

    # ---- fetch -------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str], cache_key: str) -> Any:
        cached = self.cache.get(cache_key, DEFAULT_TTL_SECONDS["fantasypros_ecr"])
        if cached is not None:
            return cached
        try:
            resp = self.client.get(
                f"{BASE}{path}", params=params, headers={"x-api-key": self.api_key}
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable("fantasypros", str(exc), required=True) from exc

        # Status before parsing. A missing or dead key answers 403 {"message":"Forbidden"},
        # which parses fine as JSON and contains nothing this module can use.
        if resp.status_code == 403:
            raise SourceUnavailable(
                "fantasypros",
                "HTTP 403. The key is missing, wrong, or the subscription lapsed.",
                required=True,
            )
        if resp.status_code != 200:
            raise SourceUnavailable(
                "fantasypros", f"HTTP {resp.status_code}: {resp.text[:200]}", required=True
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SchemaDrift(
                "fantasypros", "HTTP 200 but the body is not JSON", required=True
            ) from exc
        self.cache.set(cache_key, payload)
        return payload

    # ---- ProjectionSource --------------------------------------------------------

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        try:
            payload = self._get(
                f"/nfl/{self.season}/projections",
                {"position": "ALL", "week": str(week), "scoring": self.scoring},
                f"fantasypros_ecr:proj:{self.season}:{week}:{self.scoring}",
            )
            index = index_projections(payload)
        except (SourceUnavailable, SchemaDrift) as exc:
            self._last_error = str(exc)
            log.warning("fantasypros_unavailable", detail=str(exc))
            raise

        out: dict[PlayerId, float] = {}
        for player in players:
            value = index.get(match_key(player.name, player.position, player.team))
            if value is not None:
                out[player.id] = value
        self._last_error = None
        log.info("fantasypros_projections", week=week, asked=len(players), matched=len(out))
        return out

    def ranks(self, week: int, players: list[Player]) -> dict[PlayerId, RankConsensus]:
        """Expert consensus with the disagreement spread. Rank units, not points."""
        payload = self._get(
            f"/nfl/{self.season}/consensus-rankings",
            {
                "position": "ALL",
                "week": str(week),
                "scoring": self.scoring,
                "type": "weekly",
            },
            f"fantasypros_ecr:ranks:{self.season}:{week}:{self.scoring}",
        )
        index = index_ranks(payload)
        return {
            player.id: index[key]
            for player in players
            if (key := match_key(player.name, player.position, player.team)) in index
        }

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            ok=self._last_error is None,
            required=self.required,
            detail=self._last_error,
        )


# ---- parsing ---------------------------------------------------------------------


def _first(row: dict[str, Any], fields: tuple[str, ...]) -> Any | None:
    for field in fields:
        if row.get(field) not in (None, ""):
            return row[field]
    return None


def _players_list(payload: Any, what: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise SchemaDrift(
            "fantasypros",
            f"{what}: top level is {type(payload).__name__}, not an object",
            required=True,
        )
    players = payload.get("players")
    if not isinstance(players, list) or not players:
        raise SchemaDrift("fantasypros", f"{what}: no 'players' list", required=True)
    return [row for row in players if isinstance(row, dict)]


def _identity(row: dict[str, Any], what: str) -> str | None:
    """The match key for one row, or None when the row is not a player we model."""
    name = _first(row, _NAME_FIELDS)
    if name is None:
        raise SchemaDrift("fantasypros", f"{what}: a row has none of {_NAME_FIELDS}", required=True)
    raw_position = _first(row, _POSITION_FIELDS)
    if raw_position is None:
        raise SchemaDrift(
            "fantasypros", f"{what}: a row has none of {_POSITION_FIELDS}", required=True
        )
    position = parse_position(raw_position)
    if position is None:
        # IDP and punters show up in an ALL-position pull. Not modelled, not an error.
        return None
    team = canonical_team(str(_first(row, _TEAM_FIELDS) or ""))
    return match_key(str(name), position, team)


def index_projections(payload: Any) -> dict[str, float]:
    """``match_key -> projected points``. Validated on every fetch."""
    rows = _players_list(payload, "projections")
    out: dict[str, float] = {}
    for row in rows:
        key = _identity(row, "projections")
        if key is None:
            continue
        points = _first(row, _POINTS_FIELDS)
        if points is None:
            raise SchemaDrift(
                "fantasypros",
                f"projections: a row has none of {_POINTS_FIELDS}",
                required=True,
            )
        try:
            out[key] = float(points)
        except (TypeError, ValueError) as exc:
            raise SchemaDrift(
                "fantasypros",
                f"projections: points value {points!r} is not a number",
                required=True,
            ) from exc
    if not out:
        raise SchemaDrift("fantasypros", "projections: no rows we can model", required=True)
    return out


def index_ranks(payload: Any) -> dict[str, RankConsensus]:
    """``match_key -> RankConsensus``. The std-dev is the field worth paying for."""
    rows = _players_list(payload, "consensus-rankings")
    out: dict[str, RankConsensus] = {}
    for row in rows:
        key = _identity(row, "consensus-rankings")
        if key is None:
            continue
        if "rank_std" not in row:
            raise SchemaDrift(
                "fantasypros",
                "consensus-rankings: no 'rank_std'. That field is the reason this source "
                "is paid for; without it the pull has no value over a free ranking.",
                required=True,
            )
        try:
            out[key] = RankConsensus(
                ecr=float(row.get("rank_ecr", row.get("rank_ave", 0)) or 0),
                std_dev=float(row["rank_std"] or 0),
                best=float(row.get("rank_min", 0) or 0),
                worst=float(row.get("rank_max", 0) or 0),
                tier=int(row["tier"]) if row.get("tier") is not None else None,
            )
        except (TypeError, ValueError) as exc:
            raise SchemaDrift(
                "fantasypros", f"consensus-rankings: unreadable numbers in {key}", required=True
            ) from exc
    if not out:
        raise SchemaDrift("fantasypros", "consensus-rankings: no rows we can model", required=True)
    return out
