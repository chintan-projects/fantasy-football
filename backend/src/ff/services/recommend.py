"""Orchestration: fetch via adapters, compute via domain, decide.

No math lives here and no HTTP lives here. If you are tempted to add either, it belongs one
layer out (CLAUDE.md 2.2).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ff.core.logging import get_logger
from ff.domain.distributions import PlayerContext
from ff.domain.lineup import (
    NOISE_THRESHOLD_WIN_PROB,
    Candidate,
    contested_slots,
    decide,
    enumerate_candidate_lineups,
    greedy_lineup,
    win_probability,
)
from ff.domain.models import (
    LeagueSettings,
    LineupPlan,
    PlayerId,
    Recommendation,
    Slot,
    SlotDecision,
    SourceStatus,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WeekInputs:
    """Everything one recommendation needs, already fetched. Snapshot this and a backtest
    becomes a replay rather than a re-fetch."""

    week: int
    settings: LeagueSettings
    candidates: list[Candidate]
    my_contexts: dict[PlayerId, PlayerContext]
    opponent_contexts: list[PlayerContext]
    sources: tuple[SourceStatus, ...]
    draws: int = 20_000
    seed: int | None = None


def recommend(inputs: WeekInputs) -> Recommendation:
    """Rank the handful of plausible lineups by win probability, then explain the calls."""
    rng = np.random.default_rng(inputs.seed)
    lineups = enumerate_candidate_lineups(inputs.candidates, inputs.settings)

    scored: list[tuple[float, tuple[float, float], dict[PlayerId, Slot]]] = []
    for lineup in lineups:
        contexts = [inputs.my_contexts[pid] for pid in lineup if pid in inputs.my_contexts]
        p, band = win_probability(contexts, inputs.opponent_contexts, inputs.draws, rng)
        scored.append((p, band, lineup))

    baseline = greedy_lineup(inputs.candidates, inputs.settings)
    scored.sort(key=lambda t: t[0], reverse=True)
    best_p, best_band, best = scored[0]
    baseline_p, baseline_band = next(
        ((p, band) for p, band, lu in scored if lu == baseline), (best_p, best_band)
    )
    am_i_favored = best_p > 0.5

    # BUG-008. If the simulation cannot separate the winning lineup from the
    # highest-projection one, keep the highest-projection one. Two reasons, and the second
    # is the one that bites: a swap the model cannot justify is churn, and the per-slot
    # verdicts below are derived from the greedy lineup -- so taking a variant the
    # simulation rates as a tie produces a plan that contradicts its own explanation.
    if best_p - baseline_p < NOISE_THRESHOLD_WIN_PROB:
        best_p, best_band, best = baseline_p, baseline_band, baseline

    by_id = {c.id: c for c in inputs.candidates}
    decisions: list[SlotDecision] = []
    for slot, greedy_starter, rival in contested_slots(inputs.candidates, inputs.settings):
        # BUG-008. Explain the lineup we are recommending, not the one greedy would have
        # picked. contested_slots names the pair who compete for the slot; which of them
        # ends up starting is the plan's call, so read the incumbent off the plan.
        pair = (greedy_starter, rival)
        starter = next((pid for pid in pair if best.get(pid) is slot), None)
        alternative = next((pid for pid in pair if pid != starter), None)
        if starter is None or alternative is None:
            continue  # the plan moved one of them elsewhere; this is no longer that choice

        variant = {k: v for k, v in best.items() if k != starter}
        variant[alternative] = slot
        variant_p = next((p for p, _, lu in scored if lu == variant), None)
        if variant_p is None:
            # Never report an unsimulated swap as a dead heat -- that is a claim, and it is
            # the claim most likely to be wrong. Leave the slot out instead.
            continue
        decisions.append(
            decide(
                slot=slot,
                starter=starter,
                alternative=alternative,
                win_prob_delta=variant_p - best_p,
                starter_proj=by_id[starter].projection,
                alt_proj=by_id[alternative].projection,
                am_i_favored=am_i_favored,
            )
        )

    expected = sum(by_id[pid].projection.mean for pid in best if pid in by_id)
    plan = LineupPlan(
        week=inputs.week,
        assignments=tuple(sorted(best.items())),
        expected_points=expected,
        win_probability=best_p,
        win_probability_band=best_band,
        notes=(f"Expected-points lineup wins {baseline_p:.1%}; best simulated {best_p:.1%}.",),
    )

    caveats = _caveats(inputs, best_p, baseline_p)
    log.info("recommendation", week=inputs.week, win_prob=round(best_p, 4), swaps=len(decisions))
    return Recommendation(
        week=inputs.week,
        lineup=plan,
        decisions=tuple(decisions),
        sources=inputs.sources,
        caveats=caveats,
    )


def _caveats(inputs: WeekInputs, best_p: float, baseline_p: float) -> tuple[str, ...]:
    """Say out loud what the model cannot do. Overclaiming is how this product fails."""
    out = [
        "Weekly projections explain roughly 3-23% of the variance in actual points. "
        "Treat the ranking as a tiebreaker, not a forecast.",
    ]
    if abs(best_p - baseline_p) < 0.01:
        out.append(
            "Simulation barely separates these lineups. Starting the highest projected "
            "players would give effectively the same result this week."
        )
    stale = [s for s in inputs.sources if not s.ok]
    if stale:
        names = ", ".join(s.name for s in stale)
        out.append(f"Missing or stale: {names}. Confidence is lower than usual.")
    return tuple(out)
