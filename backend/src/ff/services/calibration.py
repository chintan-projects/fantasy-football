"""Scoring the app against what actually happened.

Everything here joins two things the app already keeps apart on purpose: the snapshot,
which is what we believed at the moment we decided, and the outcomes table, which is what
the world did afterwards. Neither is re-fetched. A projection re-read next week is not the
projection the decision used -- every source silently rewrites its own history -- so a
calibration built on live fetches measures the wrong thing and looks fine doing it.

Order of operations matters and it is the reverse of what seems natural: ``ingest`` must
run before ``week_report`` can say anything, and ``ingest`` can only score players that a
snapshot recorded. A week with no snapshot is not a bad week, it is an invisible one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ff.adapters.base import ActualsSource
from ff.adapters.store import Store
from ff.core.logging import get_logger
from ff.domain.calibration import (
    Comparison,
    LineupScore,
    SourceScore,
    compare,
    score_lineup,
    score_sources,
)
from ff.domain.models import Player, PlayerId, Position, Slot

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WeekReport:
    week: int
    sources: list[SourceScore]
    ensemble: SourceScore | None
    versus_ensemble: list[Comparison]
    lineup: LineupScore | None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SeasonReport:
    weeks_scored: list[int]
    sources: list[SourceScore]
    ensemble: SourceScore | None
    versus_ensemble: list[Comparison]
    lineups: list[LineupScore]
    note: str | None = None

    @property
    def weeks_ahead_of_baseline(self) -> int:
        return sum(1 for lineup in self.lineups if lineup.edge > 0)


def ingest(store: Store, actuals: ActualsSource, week: int, kind: str = "lineup") -> int:
    """Record what the players in a week's snapshot actually scored. Returns the count.

    Idempotent: ``record_outcome`` upserts, so re-running after the Monday night game
    corrects Sunday's partial numbers rather than duplicating them.
    """
    snapshot = store.latest_snapshot(week, kind)
    if snapshot is None:
        log.info("calibration_no_snapshot", week=week, kind=kind)
        return 0

    players = _players_from(snapshot)
    if not players:
        return 0

    scored = actuals.weekly(week, players)
    for player_id, points in scored.items():
        store.record_outcome(week, str(player_id), points)
    log.info(
        "calibration_ingested",
        week=week,
        players=len(players),
        scored=len(scored),
        source=actuals.name,
    )
    return len(scored)


def week_report(store: Store, week: int, kind: str = "lineup") -> WeekReport | None:
    """Score one week. None when there is no snapshot for it at all."""
    snapshot = store.latest_snapshot(week, kind)
    if snapshot is None:
        return None

    actual = {PlayerId(pid): points for pid, points in store.outcomes_for_week(week).items()}
    if not actual:
        return WeekReport(
            week=week,
            sources=[],
            ensemble=None,
            versus_ensemble=[],
            lineup=None,
            note=(
                f"Week {week} has inputs but no outcomes yet. Nothing can be scored until "
                f"the week's games are final and the actuals have been ingested."
            ),
        )

    forecasts = _forecasts_from(snapshot)
    sources = score_sources(forecasts, actual)
    ensemble = next((s for s in score_sources(_ensemble_from(snapshot), actual)), None)
    comparisons = [
        c
        for name in forecasts
        if name != _ENSEMBLE
        and (c := compare({**forecasts, **_ensemble_from(snapshot)}, actual, _ENSEMBLE, name))
    ]
    return WeekReport(
        week=week,
        sources=sources,
        ensemble=ensemble,
        versus_ensemble=comparisons,
        lineup=_lineup_score(store, snapshot, week, actual),
    )


def season_report(store: Store, kind: str = "lineup") -> SeasonReport:
    """Pool every scored week. The per-week numbers are too thin to read on their own.

    Pooling is the point: one week of a dozen starters cannot separate two forecasters, and
    presenting a weekly MAE table invites reading a ranking into noise. ``compare`` still
    refuses to call a winner until there are enough pairs.
    """
    pooled_forecasts: dict[str, dict[PlayerId, float]] = {}
    pooled_actual: dict[PlayerId, float] = {}
    lineups: list[LineupScore] = []
    scored_weeks: list[int] = []

    for week in store.weeks_with_snapshots(kind):
        snapshot = store.latest_snapshot(week, kind)
        actual = {PlayerId(pid): pts for pid, pts in store.outcomes_for_week(week).items()}
        if snapshot is None or not actual:
            continue
        scored_weeks.append(week)

        # Player ids repeat across weeks, so they are namespaced by week before pooling.
        # Without this, week 3 would overwrite week 2 and the sample would stay small while
        # looking like it grew.
        for name, values in {**_forecasts_from(snapshot), **_ensemble_from(snapshot)}.items():
            bucket = pooled_forecasts.setdefault(name, {})
            for pid, value in values.items():
                bucket[PlayerId(f"{week}:{pid}")] = value
        for pid, points in actual.items():
            pooled_actual[PlayerId(f"{week}:{pid}")] = points

        lineup = _lineup_score(store, snapshot, week, actual)
        if lineup is not None:
            lineups.append(lineup)

    per_source = {k: v for k, v in pooled_forecasts.items() if k != _ENSEMBLE}
    comparisons = [
        c for name in per_source if (c := compare(pooled_forecasts, pooled_actual, _ENSEMBLE, name))
    ]
    return SeasonReport(
        weeks_scored=scored_weeks,
        sources=score_sources(per_source, pooled_actual),
        ensemble=next(
            (
                s
                for s in score_sources(
                    {_ENSEMBLE: pooled_forecasts.get(_ENSEMBLE, {})}, pooled_actual
                )
            ),
            None,
        ),
        versus_ensemble=comparisons,
        lineups=lineups,
        note=None if scored_weeks else "No week has both a snapshot and recorded outcomes yet.",
    )


# ---- reading the snapshot back ------------------------------------------------------
#
# The snapshot is JSON written by ``WeekBundle.snapshot``. Everything below is the inverse
# of that method and nothing else should parse it.

_ENSEMBLE = "ensemble"


def _players_from(snapshot: dict[str, Any]) -> list[Player]:
    players: list[Player] = []
    for entry in snapshot.get("roster", []):
        position = _position(entry.get("position"))
        if position is None:
            continue
        players.append(
            Player(
                id=PlayerId(str(entry["id"])),
                name=str(entry.get("name", "")),
                position=position,
                team=str(entry.get("team") or ""),
                eligible_slots=_slots(entry.get("slots", [])),
            )
        )
    return players


def _forecasts_from(snapshot: dict[str, Any]) -> dict[str, dict[PlayerId, float]]:
    """Per-source means, transposed from player-major to source-major.

    Snapshots written before per-source values were kept have only the ensemble, and come
    back empty here rather than raising -- an old week is unscorable per source, which is
    a fact about the data rather than an error.
    """
    out: dict[str, dict[PlayerId, float]] = {}
    for player_id, projection in snapshot.get("projections", {}).items():
        for source, value in (projection.get("per_source") or {}).items():
            out.setdefault(source, {})[PlayerId(player_id)] = float(value)
    return out


def _ensemble_from(snapshot: dict[str, Any]) -> dict[str, dict[PlayerId, float]]:
    return {
        _ENSEMBLE: {
            PlayerId(pid): float(proj["mean"])
            for pid, proj in snapshot.get("projections", {}).items()
            if proj.get("mean") is not None
        }
    }


def _lineup_score(
    store: Store, snapshot: dict[str, Any], week: int, actual: dict[PlayerId, float]
) -> LineupScore | None:
    """Grade the lineup we recommended, if we recommended one and it is reconstructable."""
    recommendations = [r for r in store.list_recommendations(week=week) if r.kind == "lineup"]
    if not recommendations:
        return None
    starters = recommendations[0].payload.get("starters") or []

    recommended: dict[PlayerId, Slot] = {}
    for entry in starters:
        # Both spellings, because this reads JSON written by older code. The scheduler
        # stored two-tuples until 2026-09-13 and the tool always stored objects; rows in
        # that format are still on disk and are still perfectly gradeable.
        if isinstance(entry, dict):
            player_id, slot_name = entry.get("player_id"), entry.get("slot")
        else:
            player_id, slot_name = entry[0], entry[1]
        slot = _slot(slot_name)
        if slot is not None and player_id is not None:
            recommended[PlayerId(str(player_id))] = slot
    if not recommended:
        return None

    eligibility = {
        PlayerId(str(e["id"])): _slots(e.get("slots", []))
        for e in snapshot.get("roster", [])
        if e.get("slots")
    }
    if not eligibility:
        return None  # a snapshot from before eligibility was persisted

    projections = {
        PlayerId(pid): float(proj["mean"])
        for pid, proj in snapshot.get("projections", {}).items()
        if proj.get("mean") is not None
    }
    return score_lineup(
        week=week,
        recommended=recommended,
        projections=projections,
        eligibility=eligibility,
        slots=tuple(recommended.values()),
        actual=actual,
    )


def _position(raw: Any) -> Position | None:
    try:
        return Position(str(raw))
    except ValueError:
        return None


def _slot(raw: Any) -> Slot | None:
    try:
        return Slot(str(raw))
    except ValueError:
        return None


def _slots(raw: Any) -> frozenset[Slot]:
    return frozenset(s for s in (_slot(v) for v in raw or []) if s is not None)
