"""What a player's role actually is, from how he has been used.

Projections say what a player will score. Usage says why: how many of his team's snaps
he plays, how many of its targets and carries he gets. Two players projected for the same
11 points are not the same bet when one plays 85% of snaps and the other 40%.

What this module claims, and how strongly:

* Usage shares are steadier week to week than fantasy points, so a change in share is an
  earlier signal of a role change than a change in points. `[empirical]` -- widely
  reported in public fantasy research. **Not re-measured in this app's data**, which has
  four weeks of one season.
* The role names and the thresholds behind them (a 25% target share is a "lead target")
  are `[folk]`. They are the conventions fantasy analysts use, chosen to be readable, not
  fitted to anything.
* The trend thresholds (a 5-point rise in target share, a 10-point rise in snap share,
  last two games against the games before) are `[folk]` too.

So nothing here changes a projection, a win probability or a bid. Usage is reported next
to those numbers as the reason to trust or doubt them, and it is offered as a tiebreak only
when the projections are already too close to call -- where it is labelled as untested.
Pure: no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from statistics import mean
from typing import Literal

from ff.domain.models import Position

Trend = Literal["rising", "falling", "steady", "too few games"]

#: Games in the "recent" window the trend compares against everything before it.
RECENT_GAMES = 2

#: The smallest change in a share that counts as a trend. `[folk]`
TARGET_SHARE_SHIFT = 0.05
SNAP_SHARE_SHIFT = 0.10

#: Target share bands for receivers and tight ends, highest first. `[folk]`
TARGET_ROLES: tuple[tuple[float, str], ...] = (
    (0.25, "lead target"),
    (0.18, "regular target"),
    (0.12, "part-time target"),
    (0.0, "fringe"),
)

#: Tight ends see fewer targets than receivers -- blocking is half the job -- so the same
#: share means more. `[folk]`
TIGHT_END_ROLES: tuple[tuple[float, str], ...] = (
    (0.20, "lead target"),
    (0.14, "regular target"),
    (0.09, "part-time target"),
    (0.0, "fringe"),
)

#: Snap share bands for running backs, highest first. `[folk]`
BACKFIELD_ROLES: tuple[tuple[float, str], ...] = (
    (0.65, "workhorse"),
    (0.45, "lead back"),
    (0.30, "committee back"),
    (0.0, "backup"),
)


@dataclass(frozen=True, slots=True)
class UsageWeek:
    """One game. Shares are fractions of the team's total, 0 to 1, or None if unknown."""

    week: int
    snap_share: float | None
    targets: int
    target_share: float | None
    air_yards_share: float | None
    carries: int
    rush_share: float | None
    points: float | None

    @property
    def opportunities(self) -> int:
        """Carries plus targets: every play the ball was meant for him."""
        return self.carries + self.targets


@dataclass(frozen=True, slots=True)
class UsageProfile:
    """A season of usage, summarized for one decision."""

    games: int
    weeks: tuple[int, ...]
    snap_share: float | None
    snap_share_recent: float | None
    target_share: float | None
    target_share_recent: float | None
    air_yards_share: float | None
    rush_share: float | None
    rush_share_recent: float | None
    opportunities_per_game: float
    opportunities_recent: float
    points_per_game: float | None
    role: str
    trend: Trend
    trend_measure: str
    summary: str


def profile(name: str, position: Position, games: Sequence[UsageWeek]) -> UsageProfile | None:
    """Summarize a player's usage, or None where usage says nothing.

    Kickers and team defenses have no usage worth the name. A player with no games has
    nothing to summarize -- which is not the same as a player with no role, so it is None
    rather than an empty profile.
    """
    if position in (Position.K, Position.DEF) or not games:
        return None
    ordered = sorted(games, key=lambda g: g.week)
    recent = ordered[-RECENT_GAMES:]
    earlier = ordered[:-RECENT_GAMES]

    snap = _mean(g.snap_share for g in ordered)
    target = _mean(g.target_share for g in ordered)
    rush = _mean(g.rush_share for g in ordered)
    measure, shift = _trend_measure(position)
    trend = _trend(earlier, recent, measure, shift)
    role = _role(position, snap, target)
    opportunities = mean(g.opportunities for g in ordered)
    points = _mean(g.points for g in ordered)

    result = UsageProfile(
        games=len(ordered),
        weeks=tuple(g.week for g in ordered),
        snap_share=snap,
        snap_share_recent=_mean(g.snap_share for g in recent),
        target_share=target,
        target_share_recent=_mean(g.target_share for g in recent),
        air_yards_share=_mean(g.air_yards_share for g in ordered),
        rush_share=rush,
        rush_share_recent=_mean(g.rush_share for g in recent),
        opportunities_per_game=round(opportunities, 1),
        opportunities_recent=round(mean(g.opportunities for g in recent), 1),
        points_per_game=None if points is None else round(points, 1),
        role=role,
        trend=trend,
        trend_measure=measure.replace("_", " "),
        summary="",
    )
    return _with_summary(name, position, result, ordered[-1])


def _trend_measure(position: Position) -> tuple[str, float]:
    """The share that best describes the role at this position, and its trend threshold.

    Receivers are defined by targets. Backs and quarterbacks by whether they are on the
    field at all -- a back's targets swing with game script, his snaps much less. `[folk]`
    """
    if position in (Position.WR, Position.TE):
        return "target_share", TARGET_SHARE_SHIFT
    return "snap_share", SNAP_SHARE_SHIFT


def _trend(
    earlier: Sequence[UsageWeek], recent: Sequence[UsageWeek], measure: str, shift: float
) -> Trend:
    before = _mean(getattr(g, measure) for g in earlier)
    after = _mean(getattr(g, measure) for g in recent)
    if before is None or after is None or len(recent) < RECENT_GAMES:
        return "too few games"
    if after - before >= shift:
        return "rising"
    if before - after >= shift:
        return "falling"
    return "steady"


def _role(position: Position, snap: float | None, target: float | None) -> str:
    if position is Position.QB:
        if snap is None:
            return "unknown"
        return "starter" if snap >= 0.85 else "part-time"
    if position is Position.RB:
        return _band(BACKFIELD_ROLES, snap)
    if position is Position.TE:
        return _band(TIGHT_END_ROLES, target)
    return _band(TARGET_ROLES, target)


def _band(bands: tuple[tuple[float, str], ...], value: float | None) -> str:
    if value is None:
        return "unknown"
    for floor, label in bands:
        if value >= floor:
            return label
    return bands[-1][1]


def _with_summary(name: str, position: Position, p: UsageProfile, last: UsageWeek) -> UsageProfile:
    """One plain sentence a person can read on a phone."""
    parts = []
    if p.snap_share is not None:
        parts.append(
            f"plays {pct(p.snap_share)} of snaps"
            + _lately(p.snap_share, last.snap_share, SNAP_SHARE_SHIFT)
        )
    if position in (Position.WR, Position.TE) and p.target_share is not None:
        parts.append(
            f"gets {pct(p.target_share)} of targets"
            + _lately(p.target_share, last.target_share, TARGET_SHARE_SHIFT)
        )
    if position is Position.RB:
        if p.rush_share is not None:
            parts.append(f"gets {pct(p.rush_share)} of carries")
        parts.append(f"{p.opportunities_per_game:g} carries and targets a game")
    games = f"{p.games} game{'s' if p.games != 1 else ''}"
    trend = {
        "rising": f"{p.trend_measure} rising over the last {RECENT_GAMES}",
        "falling": f"{p.trend_measure} falling over the last {RECENT_GAMES}",
        "steady": f"{p.trend_measure} steady",
        "too few games": "too few games for a trend",
    }[p.trend]
    sentence = f"{name} {', '.join(parts)} over {games} ({p.role}); {trend}."
    return replace(p, summary=sentence)


def _lately(season: float, last_game: float | None, shift: float) -> str:
    """The last game's share too, when a season average would hide a change.

    Zay Flowers, 2026: 29% and 33% of snaps coming back from injury, then 73%. A season
    average of 45% describes none of those games.
    """
    if last_game is None or abs(last_game - season) < shift:
        return ""
    return f" ({pct(last_game)} last game)"


def _mean(values: Iterable[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return round(mean(present), 3) if present else None


def pct(share: float) -> str:
    return f"{round(share * 100)}%"
