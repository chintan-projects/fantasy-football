"""How each player has been used: snaps, targets, carries. From nflverse, via ``nflreadpy``.

Two nflverse tables, joined on name, position and week:

* ``load_player_stats(summary_level="week")`` -- targets, ``target_share``,
  ``air_yards_share``, carries, receptions and PPR points per game. Shares are nflverse's
  own, computed against the team's totals.
* ``load_snap_counts`` -- ``offense_pct``, the share of the team's offensive snaps. From
  Pro Football Reference, so names are PFR's spelling; ``match_key`` absorbs the
  differences (suffixes, punctuation).

nflverse publishes no rushing share, so it is computed here: a player's carries over his
team's carries that week, the team total summed from every row of that team-week.

Probed 2026-10-06 against the 2026 files: weeks 1-4 present in both, 4449 stat rows with
150 columns, 5970 snap rows. `[empirical]`

**Completed weeks only.** A week in progress has partial rows, and a partial row looks
like a demotion. ``weekly_usage`` takes the current week and reads only the weeks before
it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from ff.adapters._common import Scoring, canonical_team, from_ppr, match_key, parse_position
from ff.adapters.nflverse import to_rows
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, SourceStatus
from ff.domain.usage import UsageWeek

#: Read from ``load_player_stats``. Checked on every fetch.
STATS_COLUMNS = (
    "season",
    "week",
    "season_type",
    "player_display_name",
    "position",
    "team",
    "targets",
    "target_share",
    "air_yards_share",
    "carries",
    "receptions",
    "fantasy_points_ppr",
)

#: Read from ``load_snap_counts``.
SNAP_COLUMNS = ("season", "game_type", "week", "player", "position", "team", "offense_pct")

Loader = Callable[[Sequence[int]], Any]

log = get_logger(__name__)


class NflverseUsage:
    """Per-game usage for the players asked about. Optional: it explains, never decides."""

    name = "nflverse_usage"
    required = False

    def __init__(
        self,
        season: int,
        cache: FileCache,
        *,
        scoring: Scoring = "ppr",
        stats_loader: Loader | None = None,
        snaps_loader: Loader | None = None,
    ) -> None:
        self.season = season
        self.cache = cache
        self.scoring = scoring
        self._stats_loader = stats_loader
        self._snaps_loader = snaps_loader
        self._last_error: str | None = None

    @property
    def _cache_key(self) -> str:
        return f"nflverse_usage:v1:{self.season}"

    def _loaders(self) -> tuple[Loader, Loader]:
        if self._stats_loader is not None and self._snaps_loader is not None:
            return self._stats_loader, self._snaps_loader
        try:
            import nflreadpy
        except ImportError as exc:  # pragma: no cover - packaging, not runtime
            raise SourceUnavailable(
                self.name, "nflreadpy is not installed. Run 'make setup'.", required=False
            ) from exc
        return (
            self._stats_loader
            or (lambda s: nflreadpy.load_player_stats(seasons=list(s), summary_level="week")),
            self._snaps_loader or (lambda s: nflreadpy.load_snap_counts(seasons=list(s))),
        )

    def _tables(self) -> dict[str, list[dict[str, Any]]]:
        cached = self.cache.get(self._cache_key, DEFAULT_TTL_SECONDS["nflverse_usage"])
        if cached is not None:
            return dict(cached)
        stats_loader, snaps_loader = self._loaders()
        try:
            stats = to_rows(stats_loader([self.season]), self.name)
            snaps = to_rows(snaps_loader([self.season]), self.name)
        except (SourceUnavailable, SchemaDrift):
            raise
        except Exception as exc:
            raise SourceUnavailable(self.name, str(exc), required=False) from exc
        _require(stats, STATS_COLUMNS, "player stats")
        _require(snaps, SNAP_COLUMNS, "snap counts")
        tables = {
            # 150 columns down to twelve, and linemen and defenders out, so the cache file
            # is small enough to be worth having.
            "stats": [
                {c: row.get(c) for c in STATS_COLUMNS}
                for row in stats
                if parse_position(row.get("position")) is not None
            ],
            "snaps": [
                {c: row.get(c) for c in SNAP_COLUMNS}
                for row in snaps
                if parse_position(row.get("position")) is not None
            ],
        }
        self.cache.set(self._cache_key, tables)
        return tables

    def weekly_usage(
        self, current_week: int, players: list[Player]
    ) -> dict[PlayerId, list[UsageWeek]]:
        """Every completed game this season for each player asked about.

        A player with no games is absent. That covers a rookie yet to play, a player hurt
        all season and a name nflverse spells differently, and those are not the same --
        so absent means "nothing known", never "no role".
        """
        try:
            tables = self._tables()
        except (SourceUnavailable, SchemaDrift) as exc:
            self._last_error = str(exc)
            log.warning("nflverse_usage_unavailable", detail=str(exc))
            raise
        index = index_usage(
            tables["stats"], tables["snaps"], self.season, current_week, self.scoring
        )
        out: dict[PlayerId, list[UsageWeek]] = {}
        for player in players:
            games = index.get(match_key(player.name, player.position, player.team))
            if games:
                out[player.id] = games
        self._last_error = None
        log.info("nflverse_usage", week=current_week, asked=len(players), matched=len(out))
        return out

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            ok=self._last_error is None,
            required=False,
            age_seconds=self.cache.age_seconds(self._cache_key),
            detail=self._last_error,
        )


def _require(rows: list[dict[str, Any]], columns: tuple[str, ...], table: str) -> None:
    """A renamed column should say so rather than yield a season of empty usage."""
    if not rows:
        return
    missing = [c for c in columns if c not in rows[0]]
    if missing:
        raise SchemaDrift(
            "nflverse_usage", f"{table} is missing {', '.join(missing)}", required=False
        )


def index_usage(
    stats: list[dict[str, Any]],
    snaps: list[dict[str, Any]],
    season: int,
    current_week: int,
    scoring: Scoring = "ppr",
) -> dict[str, list[UsageWeek]]:
    """Join the two tables into games per player, keyed the way every source is keyed.

    Regular season only, and only weeks before ``current_week``.
    """

    def counts(row: dict[str, Any]) -> bool:
        if int(row.get("season", -1)) != season:
            return False
        kind = row.get("season_type", row.get("game_type", "REG"))
        return str(kind).upper() == "REG" and 0 < int(row.get("week", 0)) < current_week

    team_carries: dict[tuple[str, int], int] = defaultdict(int)
    for row in stats:
        if counts(row):
            team_carries[(canonical_team(row.get("team")), int(row["week"]))] += int(
                row.get("carries") or 0
            )

    snap_share: dict[tuple[str, int], float] = {}
    for row in snaps:
        position = parse_position(row.get("position"))
        if position is None or not counts(row) or row.get("offense_pct") is None:
            continue
        key = match_key(str(row.get("player", "")), position, canonical_team(row.get("team")))
        snap_share[(key, int(row["week"]))] = float(row["offense_pct"])

    out: dict[str, list[UsageWeek]] = defaultdict(list)
    for row in stats:
        position = parse_position(row.get("position"))
        name = row.get("player_display_name")
        if position is None or not name or not counts(row):
            continue
        week = int(row["week"])
        team = canonical_team(row.get("team"))
        key = match_key(str(name), position, team)
        carries = int(row.get("carries") or 0)
        total_carries = team_carries.get((team, week), 0)
        ppr = row.get("fantasy_points_ppr")
        out[key].append(
            UsageWeek(
                week=week,
                snap_share=snap_share.get((key, week)),
                targets=int(row.get("targets") or 0),
                target_share=_share(row.get("target_share")),
                air_yards_share=_share(row.get("air_yards_share")),
                carries=carries,
                rush_share=carries / total_carries if total_carries else None,
                points=None
                if ppr is None
                else round(from_ppr(float(ppr), float(row.get("receptions") or 0), scoring), 2),
            )
        )
    return dict(out)


def _share(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
