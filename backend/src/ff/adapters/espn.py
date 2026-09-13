"""ESPN weekly projections, from the undocumented ``kona_player_info`` view.

**ESPN is never a required source.** It is a scrape of an unversioned, undocumented,
unlicensed endpoint with no contract and no deprecation notice. ESPN has silently moved
hosts before (``fantasy.espn.com`` -> ``lm-api-reads.fantasy.espn.com``) and tightened
caps. So: validate on every fetch, cache hard, and degrade to a confidence note in the UI
rather than failing a recommendation. See docs/DATA_SOURCES.md.

The decoder ring, verified live on 2026-09-12:

* ``statSourceId`` 0 = actual, **1 = ESPN's projection**
* ``statSplitTypeId`` 1 = a single week, 0 = a season total
* ``scoringPeriodId`` = the week number, and ``seasonId`` = the season. Both matter: a
  response carries last season's weeks alongside this season's, under the same week
  numbers.
* ``appliedTotal`` is the fantasy-scored value; ``leaguedefaults/3`` is standard PPR.

One correction to the note in docs/DATA_SOURCES.md: the ``X-Fantasy-Filter`` header is
required, but a bare ``limit`` is now rejected with
``400 "Filter: Limit request must be accompanied by a sort"``. A sort key has to go with
it. This module sorts by percent owned, which needs no season-coded value.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ff.adapters._common import ESPN_POSITION_BY_ID, ESPN_TEAM_BY_ID, canonical_team, match_key
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, SourceStatus

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

#: ESPN's own marker for "this number is a projection, not a result".
PROJECTION_SOURCE_ID = 1

#: A single week, as opposed to a season total.
WEEKLY_SPLIT_ID = 1

log = get_logger(__name__)


class EspnProjections:
    """A ``ProjectionSource`` backed by ESPN. Optional by construction."""

    name = "espn"
    required = False

    def __init__(
        self,
        season: int,
        cache: FileCache,
        *,
        client: httpx.Client | None = None,
        player_limit: int = 800,
    ) -> None:
        self.season = season
        self.cache = cache
        self.client = client or httpx.Client(timeout=30.0)
        self.player_limit = player_limit
        self._last_error: str | None = None

    # ---- fetch -------------------------------------------------------------------

    def _filter_header(self) -> str:
        # A limit without a sort is a 400. Percent owned is stable and needs no season key.
        return json.dumps(
            {
                "players": {
                    "limit": self.player_limit,
                    "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                }
            }
        )

    def _fetch(self) -> Any:
        key = f"espn_projections:{self.season}:{self.player_limit}"
        cached = self.cache.get(key, DEFAULT_TTL_SECONDS["espn_projections"])
        if cached is not None:
            return cached

        url = f"{BASE}/seasons/{self.season}/segments/0/leaguedefaults/3?view=kona_player_info"
        try:
            resp = self.client.get(url, headers={"X-Fantasy-Filter": self._filter_header()})
        except httpx.HTTPError as exc:
            raise SourceUnavailable("espn", str(exc), required=False) from exc

        # Status before parsing. ESPN answers a bad filter with a JSON error body and a
        # blocked request with HTML, and neither is what the parser below expects.
        if resp.status_code != 200:
            raise SourceUnavailable(
                "espn", f"HTTP {resp.status_code}: {resp.text[:200]}", required=False
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SchemaDrift("espn", "HTTP 200 but the body is not JSON", required=False) from exc

        validate(payload)
        self.cache.set(key, payload)
        return payload

    # ---- ProjectionSource --------------------------------------------------------

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        """Projected points for this week, keyed by the Yahoo player id we were given.

        Players ESPN does not cover are simply absent from the result. A missing player is
        a thinner ensemble for that player, not a failed fetch.
        """
        try:
            payload = self._fetch()
        except (SourceUnavailable, SchemaDrift) as exc:
            self._last_error = str(exc)
            log.warning("espn_unavailable", detail=str(exc))
            raise

        by_key = index_by_match_key(payload, self.season, week)
        out: dict[PlayerId, float] = {}
        for player in players:
            value = by_key.get(match_key(player.name, player.position, player.team))
            if value is not None:
                out[player.id] = value
        self._last_error = None
        log.info("espn_projections", week=week, asked=len(players), matched=len(out))
        return out

    def status(self) -> SourceStatus:
        key = f"espn_projections:{self.season}:{self.player_limit}"
        return SourceStatus(
            name=self.name,
            ok=self._last_error is None,
            required=self.required,
            age_seconds=self.cache.age_seconds(key),
            detail=self._last_error,
        )


def validate(payload: Any) -> None:
    """Check the shape on every fetch, not at startup.

    ESPN changes without notice, and the failure mode that matters is not an exception --
    it is a 200 whose shape drifted just enough that the parse below yields nothing and
    the app silently runs on one projection source. So this asserts that projections are
    actually present, not merely that the JSON parsed.
    """
    if not isinstance(payload, dict):
        raise SchemaDrift(
            "espn", f"top level is {type(payload).__name__}, not an object", required=False
        )
    players = payload.get("players")
    if not isinstance(players, list) or not players:
        raise SchemaDrift("espn", "no 'players' list in the response", required=False)

    for entry in players:
        player = entry.get("player") if isinstance(entry, dict) else None
        if not isinstance(player, dict):
            continue
        stats = player.get("stats")
        if not isinstance(stats, list):
            raise SchemaDrift("espn", "a player carries no 'stats' list", required=False)
        for required_field in ("id", "fullName", "defaultPositionId"):
            if required_field not in player:
                raise SchemaDrift("espn", f"a player has no '{required_field}'", required=False)
        if any(
            isinstance(s, dict) and s.get("statSourceId") == PROJECTION_SOURCE_ID for s in stats
        ):
            return

    raise SchemaDrift(
        "espn",
        f"{len(players)} players returned and not one carries a "
        f"statSourceId={PROJECTION_SOURCE_ID} projection",
        required=False,
    )


def index_by_match_key(payload: Any, season: int, week: int) -> dict[str, float]:
    """Flatten the response to ``match_key -> projected points`` for one week.

    Filtering on ``seasonId`` is not optional: the same response carries last season's
    week 2 next to this season's, both with ``scoringPeriodId`` 2.
    """
    out: dict[str, float] = {}
    for entry in payload.get("players", []):
        player = entry.get("player") if isinstance(entry, dict) else None
        if not isinstance(player, dict):
            continue
        position = ESPN_POSITION_BY_ID.get(int(player.get("defaultPositionId", -1)))
        if position is None:
            continue
        team = canonical_team(ESPN_TEAM_BY_ID.get(int(player.get("proTeamId", -1))))
        points = weekly_projection(player.get("stats", []), season, week)
        if points is None:
            continue
        out[match_key(str(player.get("fullName", "")), position, team)] = points
    return out


def weekly_projection(stats: Any, season: int, week: int) -> float | None:
    if not isinstance(stats, list):
        return None
    for row in stats:
        if not isinstance(row, dict):
            continue
        if (
            row.get("statSourceId") == PROJECTION_SOURCE_ID
            and row.get("statSplitTypeId") == WEEKLY_SPLIT_ID
            and int(row.get("scoringPeriodId", -1)) == week
            and int(row.get("seasonId", -1)) == season
        ):
            applied = row.get("appliedTotal")
            if applied is None:
                return None
            # ESPN renders "we have no projection" as 0.0 with an empty stat line, and it
            # does this for 22% of the players it returns -- everyone listed OUT, plus
            # anyone it has not modelled yet. Blending that zero against another source's
            # real number halves it, which is worse than having no number at all. A zero
            # that DOES carry a stat line is a genuine forecast and is kept. (BUG-007.)
            if float(applied) == 0.0 and not row.get("stats"):
                return None
            return float(applied)
    return None
