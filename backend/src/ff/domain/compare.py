"""Which of a handful of players to start, or to pick up, on this week's numbers.

The lineup tool answers "who starts" by win probability across the whole roster. This
answers the narrower question the owner actually types -- "Flowers or Burden?" -- for
players who may not even be on the roster yet. So it ranks on projected points, the one
number every option has, and is plain about the limits:

* A gap under ``NOISE_THRESHOLD_POINTS`` is too close to call. Weekly projections miss by
  about 5 points, so a 1.5-point edge is noise. `[empirical]` (CLAUDE.md 3)
* When it is too close, usage is offered as a lean: the player whose share is rising.
  That rests on usage being steadier than points `[empirical]`, but the lean itself has
  not been backtested in this app `[theory]`, and the verdict says so.
* Floor and ceiling are offered for the other tiebreak that is real: a heavy favorite
  wants the floor, a heavy underdog the ceiling. `[theory]` (CLAUDE.md 4)

Pure. The service collects the numbers; this only decides.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ff.domain.lineup import NOISE_THRESHOLD_POINTS


@dataclass(frozen=True, slots=True)
class Option:
    """One player as the comparison sees him."""

    name: str
    points: float | None
    floor: float | None = None
    ceiling: float | None = None
    trend: str | None = None
    trend_measure: str | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    pick: str | None
    too_close: bool
    gap: float | None
    ranked: tuple[str, ...]
    reason: str
    lean: str | None = None
    floor_vs_ceiling: str | None = None


def compare(options: Sequence[Option]) -> Verdict:
    """Rank by projected points; refuse to pick inside the noise."""
    projected = sorted(
        (o for o in options if o.points is not None),
        key=lambda o: o.points or 0.0,
        reverse=True,
    )
    missing = [o.name for o in options if o.points is None]
    ranked = tuple(o.name for o in projected) + tuple(missing)
    unprojected = (
        f" No projection for {_names(missing)}, so {'it is' if len(missing) == 1 else 'they are'}"
        f" not ranked."
        if missing
        else ""
    )

    if not projected:
        return Verdict(
            pick=None,
            too_close=False,
            gap=None,
            ranked=ranked,
            reason="None of these players has a projection from two or more sources, so "
            "there is nothing to compare." + unprojected,
        )
    if len(projected) == 1:
        only = projected[0]
        return Verdict(
            pick=only.name,
            too_close=False,
            gap=None,
            ranked=ranked,
            reason=f"{only.name} is the only one with a projection ({only.points:.1f})."
            + unprojected,
        )

    first, second = projected[0], projected[1]
    gap = round((first.points or 0.0) - (second.points or 0.0), 1)
    tied = [
        o for o in projected if (first.points or 0.0) - (o.points or 0.0) < NOISE_THRESHOLD_POINTS
    ]

    if len(tied) == 1:
        return Verdict(
            pick=first.name,
            too_close=False,
            gap=gap,
            ranked=ranked,
            reason=f"{first.name} projects {first.points:.1f}, {gap:.1f} more than "
            f"{second.name} ({second.points:.1f}). That is outside the "
            f"{NOISE_THRESHOLD_POINTS:g}-point noise band, though weekly projections still "
            f"miss by about 5 points." + unprojected,
            floor_vs_ceiling=_floor_vs_ceiling(projected[:2]),
        )

    return Verdict(
        pick=None,
        too_close=True,
        gap=gap,
        ranked=ranked,
        reason=f"Too close to call. {_names([o.name for o in tied])} project within "
        f"{NOISE_THRESHOLD_POINTS:g} points of each other ({_points(tied)}). Projections "
        f"miss by about 5 points a week, so the order is noise." + unprojected,
        lean=_usage_lean(tied),
        floor_vs_ceiling=_floor_vs_ceiling(tied),
    )


def _usage_lean(tied: Sequence[Option]) -> str | None:
    """The one player whose role is growing, if exactly one is."""
    rising = [o for o in tied if o.trend == "rising"]
    if len(rising) != 1:
        return None
    pick = rising[0]
    return (
        f"If you want a tiebreak: {pick.name}'s {pick.trend_measure or 'usage'} is rising. "
        f"A growing role tends to show up in usage before it shows up in points, but this "
        f"lean has not been tested against results in this app. [theory]"
    )


def _floor_vs_ceiling(options: Sequence[Option]) -> str | None:
    """Name the safer and the higher-upside player, when they are different people."""
    with_range = [o for o in options if o.floor is not None and o.ceiling is not None]
    if len(with_range) < 2:
        return None
    safest = max(with_range, key=lambda o: o.floor or 0.0)
    boldest = max(with_range, key=lambda o: o.ceiling or 0.0)
    if safest.name == boldest.name:
        return (
            f"{safest.name} has both the higher floor ({safest.floor:.1f}) and the higher "
            f"ceiling ({safest.ceiling:.1f})."
        )
    return (
        f"If you expect to win comfortably, {safest.name} has the higher floor "
        f"({safest.floor:.1f}). If you need a big week to catch up, {boldest.name} has the "
        f"higher ceiling ({boldest.ceiling:.1f}). [theory]"
    )


def _names(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _points(options: Sequence[Option]) -> str:
    return ", ".join(f"{o.name} {o.points:.1f}" for o in options)
