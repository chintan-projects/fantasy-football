"""Assemble one week's inputs from the adapters, for every decision that needs them.

Three callers share this: the lineup recommendation, the waiver board, and the bid
recommendation. All three need the same league settings, the same roster, and the same
blended projections, and fetching them three times would triple the Yahoo call count for
no benefit -- Yahoo throttles per application id (CLAUDE.md 2.4), so a redundant read is
not free.

No math here. This fetches and shapes; ``domain`` decides.

**One gap, stated because it changes a number the user sees.** ``PlayerContext`` carries an
``opponent_team`` so the simulator can apply the negative game-script correlation between
players on opposite sides of the same game. Nothing in the current adapter set knows the
NFL schedule, so that field is left empty and only same-team correlation applies. The
effect is a slightly *wider* margin distribution than reality, which pulls win probability
toward 50%. It makes the model marginally less decisive, not more. `[theory]`
"""

from __future__ import annotations

from dataclasses import dataclass

from ff.adapters.base import LeagueReader, ProjectionSource
from ff.core.logging import get_logger
from ff.domain.distributions import PlayerContext
from ff.domain.lineup import Candidate
from ff.domain.models import (
    LeagueSettings,
    Player,
    PlayerId,
    Projection,
    Roster,
    SourceStatus,
)
from ff.services.projections import build_projections
from ff.services.recommend import WeekInputs

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WeekBundle:
    """Everything fetched for one week, once."""

    week: int
    settings: LeagueSettings
    roster: Roster
    projections: dict[PlayerId, Projection]
    unprojected: tuple[PlayerId, ...]
    sources: tuple[SourceStatus, ...]
    notes: tuple[str, ...]
    opponent_team_key: str | None = None
    opponent_starters: tuple[Player, ...] = ()

    def snapshot(self) -> dict[str, object]:
        """The input set, flattened for ``store.save_snapshot``.

        A backtest replays this. It never re-fetches, because by then every source will
        have changed its mind and the replay would be measuring the wrong thing.
        """
        return {
            "week": self.week,
            "league_key": str(self.settings.league_key),
            "roster": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "position": p.position.value,
                    "team": p.team,
                    # Eligibility, because hindsight scoring has to rebuild the lineup that
                    # was legal at the time. Slot rules are a league setting and they can be
                    # edited mid-season, so re-deriving them later would grade the decision
                    # against rules that were not in force when it was made.
                    "slots": sorted(s.value for s in p.eligible_slots),
                }
                for p in self.roster.players
            ],
            "projections": {
                str(pid): {
                    "mean": proj.mean,
                    "epistemic_sd": proj.epistemic_sd,
                    "aleatoric_sd": proj.aleatoric_sd,
                    "sources": list(proj.sources),
                    "per_source": dict(proj.per_source),
                }
                for pid, proj in self.projections.items()
            },
            "unprojected": [str(p) for p in self.unprojected],
            "opponent_team_key": self.opponent_team_key,
            "opponent_starters": [str(p.id) for p in self.opponent_starters],
            "sources": [
                {"name": s.name, "ok": s.ok, "required": s.required, "age_seconds": s.age_seconds}
                for s in self.sources
            ],
            "notes": list(self.notes),
        }


def context_for(player: Player, projection: Projection) -> PlayerContext:
    """One player, ready for the simulator. ``opponent_team`` is empty -- see module docs."""
    return PlayerContext(
        player_id=player.id,
        position=player.position,
        nfl_team=player.team,
        opponent_team="",
        projection=projection,
    )


def load_week(
    yahoo: LeagueReader,
    sources: list[ProjectionSource],
    week: int,
    *,
    with_opponent: bool = True,
) -> WeekBundle:
    """Fetch settings, roster, matchup and projections for one week.

    Yahoo calls are serialized inside the client, so these run one at a time on purpose.
    """
    settings = yahoo.league_settings()
    roster = yahoo.roster(week)

    opponent_key: str | None = None
    opponent_players: tuple[Player, ...] = ()
    if with_opponent:
        matchup = yahoo.matchup(week)
        opponent_key = str(matchup.opponent)
        opponent_roster = yahoo.roster(week, opponent_key)
        opponent_players = tuple(spot.player for spot in opponent_roster.starters)

    everyone = list(roster.players) + list(opponent_players)
    blended = build_projections(sources, week, everyone)

    log.info(
        "week_loaded",
        week=week,
        roster=len(roster.players),
        opponent_starters=len(opponent_players),
        projected=len(blended.projections),
    )
    return WeekBundle(
        week=week,
        settings=settings,
        roster=roster,
        projections=blended.projections,
        unprojected=blended.unprojected,
        sources=blended.sources,
        notes=blended.notes,
        opponent_team_key=opponent_key,
        opponent_starters=opponent_players,
    )


def to_week_inputs(
    bundle: WeekBundle, *, draws: int = 20_000, seed: int | None = None
) -> WeekInputs:
    """Shape a bundle into what ``services.recommend`` consumes.

    Players with no projection are dropped from the candidate set rather than given a
    guess. A player we cannot project is a player we cannot rank, and starting him on an
    invented number is worse than saying so -- which the caller does, from ``unprojected``.
    """
    candidates = [
        Candidate(player=p, projection=bundle.projections[p.id])
        for p in bundle.roster.players
        if p.id in bundle.projections
    ]
    my_contexts = {c.id: context_for(c.player, c.projection) for c in candidates}
    opponent_contexts = [
        context_for(p, bundle.projections[p.id])
        for p in bundle.opponent_starters
        if p.id in bundle.projections
    ]
    return WeekInputs(
        week=bundle.week,
        settings=bundle.settings,
        candidates=candidates,
        my_contexts=my_contexts,
        opponent_contexts=opponent_contexts,
        sources=bundle.sources,
        draws=draws,
        seed=seed,
    )
