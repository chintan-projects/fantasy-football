"""Player ratings: the comparison tool, and the same evidence attached to the board and
the lineup.

``ff_compare_players`` is a decision tool like the others. It returns a pick or "too close
to call", snapshots its inputs, and carries each player's full vector as the reasoning:
projection with floor and ceiling, all eight forecasters, usage, adds. Claude passes the
verdict on; the vector is there so the owner can see why, not so Claude can re-decide.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ff.adapters.manual import PlayerDirectory, PlayerEntry
from ff.api.deps import Deps
from ff.core.errors import FFError
from ff.domain.compare import compare
from ff.domain.distributions import floor_ceiling
from ff.domain.models import Player, PlayerId, Projection, Recommendation
from ff.domain.usage import UsageProfile
from ff.services import ratings as rating_service
from ff.services.league_entry import match_problem
from ff.services.week import WeekBundle

RATINGS_INSTRUCTIONS = """

To compare players -- "Flowers or Burden?", "who is the better pickup?" -- call
ff_compare_players. It returns a verdict and each player's evidence: the blended projection
with a floor and ceiling, what every forecaster said, how the player has been used (share
of snaps, targets and carries, and whether that share is rising), and how many Sleeper
managers added him in the last day. Pass the verdict on as given, including "too close to
call". Usage explains a projection; it does not overrule one."""

_COMPUTES = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)


class PlayerAsk(BaseModel):
    """One player to compare. Position and team settle names that two players share."""

    name: str = Field(description="The player's name, e.g. 'Zay Flowers'.")
    position: str | None = Field(default=None, description="QB, RB, WR, TE, K or DEF.")
    team: str | None = Field(default=None, description="NFL team abbreviation, e.g. BAL.")


def register(mcp: FastMCP, d: Deps) -> None:
    """Add the comparison tool to a server."""

    @mcp.tool(
        name="ff_compare_players",
        title="Compare players",
        annotations=_COMPUTES,
        description=(
            "Compare two to six players for this week -- for a start/sit call or a pickup "
            "-- and return a verdict with each player's evidence.\n\n"
            "Ranks on the blended projection: ESPN, Sleeper and six sites from Firecrawl's "
            "board (Draft Sharks, CBS, FFToday, Fantasy Football Calculator, StartWho, "
            "FantasyData), averaged. A simple average beat individual sources in 63% of "
            "comparisons. [empirical] A gap under 2 points is returned as too close to "
            "call, because weekly projections miss by about 5. [empirical]\n\n"
            "Each player also carries a floor and ceiling (10th and 90th percentile), "
            "every source's number, usage from nflverse (snap, target and carry share, and "
            "the trend over the last two games) and Sleeper's 24-hour add count. When the "
            "call is too close, the verdict may offer a usage lean; that lean is untested "
            "here and labelled [theory].\n\n"
            "For the whole lineup use ff_recommend_lineup, which decides by win "
            "probability. This tool does not know the matchup. The inputs are snapshotted."
        ),
    )
    def ff_compare_players(
        players: Annotated[
            list[PlayerAsk], Field(description="Two to six players.", min_length=2, max_length=6)
        ],
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = week if week is not None else d.sleeper.current_week()
        directory = PlayerDirectory(d.sleeper.players())
        matches = [directory.resolve(PlayerEntry(p.name, p.position, p.team)) for p in players]
        problems = [match_problem(m) for m in matches if not m.ok]
        if problems:
            return {
                "week": wk,
                "verdict": None,
                "problems": problems,
                "next_step": "Nothing was compared. Fix the names listed and ask again.",
            }
        resolved = [m.player for m in matches if m.player is not None]

        trending, trend_notes = rating_service.trending_by_key(
            lambda: d.sleeper.trending_adds(hours=24, limit=rating_service.TRENDING_LIMIT),
            d.sleeper.players,
        )
        result = rating_service.rate(
            resolved, wk, d.sources, usage=d.usage, trending=trending or None
        )
        verdict = compare([r.option() for r in result.ratings])

        payload: dict[str, Any] = {
            "week": wk,
            "verdict": {
                "pick": verdict.pick,
                "too_close_to_call": verdict.too_close,
                "gap_points": verdict.gap,
                "ranked": list(verdict.ranked),
                "reason": verdict.reason,
                "usage_lean": verdict.lean,
                "floor_or_ceiling": verdict.floor_vs_ceiling,
            },
            "players": [vector(r) for r in result.ratings],
            "corrected": [
                f"Read '{m.entry.name}' as {m.player.name}."
                for m in matches
                if m.player is not None and m.how == "close"
            ],
            "sources": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in result.sources],
            "caveats": [
                "This ranks on projected points and does not know your matchup. For the "
                "lineup itself, ff_recommend_lineup decides by win probability.",
                *result.notes,
                *trend_notes,
            ],
        }
        snapshot_id = d.store.save_snapshot(wk, "compare", payload)
        payload["recommendation_id"] = d.store.save_recommendation(
            wk, "compare", payload, verdict.reason, snapshot_id=snapshot_id
        )
        return payload


# ---- shaping ----------------------------------------------------------------------


def vector(rating: rating_service.PlayerRating) -> dict[str, Any]:
    """One player's evidence, shaped for a phone screen and for the record."""
    p = rating.player
    return {
        "player": p.name,
        "position": p.position.value,
        "nfl_team": p.team,
        "injury_status": p.injury_status,
        "projection": projection_block(rating.projection),
        "usage": usage_block(rating.usage),
        "adds_24h": rating.adds_24h,
    }


def projection_block(projection: Projection | None) -> dict[str, Any] | None:
    if projection is None:
        return None
    floor, ceiling = floor_ceiling(projection)
    by_source = dict(projection.per_source)
    values = list(by_source.values())
    return {
        "points": round(projection.mean, 1),
        "floor": floor,
        "ceiling": ceiling,
        "sources": len(values),
        "low": round(min(values), 1) if values else None,
        "high": round(max(values), 1) if values else None,
        "by_source": {name: round(value, 1) for name, value in sorted(by_source.items())},
    }


def usage_block(usage: UsageProfile | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        "summary": usage.summary,
        "role": usage.role,
        "trend": usage.trend,
        "games": usage.games,
        "snap_share": usage.snap_share,
        "snap_share_last_2": usage.snap_share_recent,
        "target_share": usage.target_share,
        "target_share_last_2": usage.target_share_recent,
        "air_yards_share": usage.air_yards_share,
        "carry_share": usage.rush_share,
        "carry_share_last_2": usage.rush_share_recent,
        "carries_plus_targets_per_game": usage.opportunities_per_game,
        "points_per_game": usage.points_per_game,
    }


def usage_for(d: Deps, week: int, players: list[Player]) -> tuple[dict[PlayerId, Any], list[str]]:
    """Usage blocks for a set of players, for the board and the lineup. Never raises."""
    try:
        profiles, notes = rating_service.usage_profiles(d.usage, week, players)
    except FFError as exc:
        return {}, [f"Usage data was unavailable ({exc})."]
    return {pid: usage_block(prof) for pid, prof in profiles.items()}, notes


def contested_usage(
    d: Deps, week: int, bundle: WeekBundle, result: Recommendation
) -> tuple[dict[str, Any], list[str]]:
    """Usage for the players in contested slots, by name: the evidence behind a close call.

    Both lineup writers call this -- the tool and the Sunday job -- so they persist one
    shape (BUG-013).
    """
    contested = {
        pid
        for decision in result.decisions
        for pid in (decision.winner, decision.runner_up)
        if pid is not None
    }
    players = [p for p in bundle.roster.players if p.id in contested]
    usage, notes = usage_for(d, week, players)
    names = {p.id: p.name for p in players}
    return {names[pid]: block for pid, block in usage.items()}, notes
