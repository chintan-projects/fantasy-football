"""Everything known about a player this week, in one record.

The input vector behind a start/sit or pickup decision: the blended projection with its
floor and ceiling, what each forecaster said, how the player has been used, how many
managers are adding him. Each piece comes from a different source and any of them can be
missing; a missing piece is left empty and named in ``notes``, never filled with a guess.

Only the projection decides anything (``domain.compare``). The rest is evidence for the
owner to weigh, labelled with how much weight it has earned -- see ``domain.usage``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ff.adapters._common import match_key
from ff.adapters.base import ProjectionSource
from ff.adapters.nflverse_usage import NflverseUsage
from ff.core.errors import FFError, SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.compare import Option
from ff.domain.distributions import floor_ceiling
from ff.domain.models import Player, PlayerId, Projection, SourceStatus
from ff.domain.usage import UsageProfile, profile
from ff.services.projections import build_projections

log = get_logger(__name__)

#: How many of Sleeper's most-added players to read. Sleeper's own list; past ~100 the
#: counts are a handful of adds each and say nothing.
TRENDING_LIMIT = 100


@dataclass(frozen=True, slots=True)
class PlayerRating:
    player: Player
    projection: Projection | None
    floor: float | None
    ceiling: float | None
    usage: UsageProfile | None
    adds_24h: int | None

    def option(self) -> Option:
        return Option(
            name=self.player.name,
            points=None if self.projection is None else round(self.projection.mean, 1),
            floor=self.floor,
            ceiling=self.ceiling,
            trend=self.usage.trend if self.usage else None,
            trend_measure=self.usage.trend_measure if self.usage else None,
        )


@dataclass(frozen=True, slots=True)
class Ratings:
    week: int
    ratings: tuple[PlayerRating, ...]
    sources: tuple[SourceStatus, ...]
    notes: tuple[str, ...]


def usage_profiles(
    usage: NflverseUsage | None, week: int, players: list[Player]
) -> tuple[dict[PlayerId, UsageProfile], list[str]]:
    """Usage for each player, or an empty map and a note saying why there is none."""
    if usage is None:
        return {}, ["Usage data is not configured on this server."]
    try:
        games = usage.weekly_usage(week, players)
    except (SourceUnavailable, SchemaDrift) as exc:
        return {}, [f"Usage data was unavailable ({exc}). Ratings show projections only."]
    out: dict[PlayerId, UsageProfile] = {}
    for player in players:
        found = profile(player.name, player.position, games.get(player.id, []))
        if found is not None:
            out[player.id] = found
    return out, []


def trending_by_key(
    trending: Callable[[], dict[str, int]] | None,
    directory: Callable[[], list[Player]] | None,
) -> tuple[dict[str, int], list[str]]:
    """Sleeper's 24-hour add counts, keyed for joining to any league's players.

    Sleeper names players by its own ids, so the counts are joined through its player
    list on name and position, the way every source is.
    """
    if trending is None or directory is None:
        return {}, []
    try:
        counts = trending()
        players = {str(p.id): p for p in directory()}
    except FFError as exc:
        return {}, [f"Sleeper's add counts were unavailable ({exc})."]
    out: dict[str, int] = {}
    for sleeper_id, count in counts.items():
        player = players.get(str(sleeper_id))
        if player is not None:
            out[match_key(player.name, player.position, player.team)] = count
    return out, []


def rate(
    players: list[Player],
    week: int,
    sources: list[ProjectionSource],
    *,
    usage: NflverseUsage | None = None,
    trending: dict[str, int] | None = None,
) -> Ratings:
    """Build the vector for each player. Projections are required; the rest degrade."""
    blended = build_projections(sources, week, players)
    profiles, usage_notes = usage_profiles(usage, week, players)
    adds = trending or {}

    ratings: list[PlayerRating] = []
    for player in players:
        projection = blended.projections.get(player.id)
        floor, ceiling = floor_ceiling(projection) if projection else (None, None)
        key = match_key(player.name, player.position, player.team)
        ratings.append(
            PlayerRating(
                player=player,
                projection=projection,
                floor=floor,
                ceiling=ceiling,
                usage=profiles.get(player.id),
                adds_24h=adds.get(key) if trending is not None else None,
            )
        )
    log.info(
        "players_rated",
        week=week,
        players=len(players),
        projected=len(blended.projections),
        with_usage=len(profiles),
    )
    return Ratings(
        week=week,
        ratings=tuple(ratings),
        sources=blended.sources,
        notes=(*blended.notes, *usage_notes),
    )
