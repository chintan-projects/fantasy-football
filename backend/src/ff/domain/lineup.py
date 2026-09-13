"""Lineup assembly and start/sit decisions.

Two separate problems, and confusing them is the standard mistake:

1. *Assembly* under expected points is a matroid optimization, so greedy by projection is
   provably optimal. It is a sort, not an optimizer. Do not import an ILP solver.
2. *Choosing between candidate lineups* under win probability is neither linear nor
   separable, so no matching algorithm can solve it. That needs Monte Carlo. But only 1-2
   slots are ever genuinely contested, so enumerate the handful of candidates exhaustively.

See .claude/skills/fantasy-decision-math, sections 1 and 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np

from ff.domain.distributions import PlayerContext, simulate
from ff.domain.models import (
    SLOT_ELIGIBILITY,
    Confidence,
    LeagueSettings,
    Player,
    PlayerId,
    Projection,
    Slot,
    SlotDecision,
)

#: Below this projected-points gap the two players are indistinguishable. Weekly projection
#: MAE is about 5 points, so a sub-2-point gap is inside the noise and the honest answer is
#: "too close to call". CLAUDE.md section 3 makes this a product requirement.
NOISE_THRESHOLD_POINTS = 2.0

#: Below this win-probability delta a change is not worth making.
NOISE_THRESHOLD_WIN_PROB = 0.005

#: Typical SD of a head-to-head margin (difference of two full-lineup totals).
#: Used only for quick analytic estimates; the simulator does the real work.
MARGIN_SD = 40.0


@dataclass(frozen=True, slots=True)
class Candidate:
    """One player available to start, with the slots he can legally fill."""

    player: Player
    projection: Projection

    @property
    def id(self) -> PlayerId:
        return self.player.id


def _slot_order(slots: tuple[Slot, ...]) -> list[Slot]:
    """Fill the most restrictive slots first.

    On a matroid greedy is optimal regardless of order, but filling dedicated positions
    before flex makes the result match how a human would read it and keeps the assignment
    deterministic.
    """
    return sorted(slots, key=lambda s: len(SLOT_ELIGIBILITY[s]))


def greedy_lineup(
    candidates: list[Candidate],
    settings: LeagueSettings,
) -> dict[PlayerId, Slot]:
    """Maximize expected points. Provably optimal for nested slot eligibility.

    The eligibility sets are laminar (FLEX contains RB, WR and TE; every other slot is a
    single position), which makes the feasible lineups the independent sets of a transversal
    matroid. Greedy by weight is optimal on a matroid. There is no cleverer arrangement.

    This stops being true if the league adds a position *limit* ("at most 3 WR") or any
    non-nested constraint. Then switch to min-cost max-flow; see docs/DECISIONS.md.
    """
    by_points = sorted(candidates, key=lambda c: c.projection.mean, reverse=True)
    used: set[PlayerId] = set()
    assignment: dict[PlayerId, Slot] = {}

    for slot in _slot_order(settings.starting_slots):
        for cand in by_points:
            if cand.id in used or not cand.player.can_fill(slot):
                continue
            assignment[cand.id] = slot
            used.add(cand.id)
            break
    return assignment


def brute_force_lineup(
    candidates: list[Candidate],
    settings: LeagueSettings,
) -> float:
    """Best achievable expected points, by exhaustive search. Test oracle only.

    Exponential. Used by the property test that proves greedy is optimal; never call it
    in application code.
    """
    slots = list(settings.starting_slots)
    best = 0.0

    def search(i: int, used: frozenset[PlayerId], total: float) -> None:
        nonlocal best
        if i == len(slots):
            best = max(best, total)
            return
        filled = False
        for c in candidates:
            if c.id in used or not c.player.can_fill(slots[i]):
                continue
            filled = True
            search(i + 1, used | {c.id}, total + c.projection.mean)
        if not filled:
            search(i + 1, used, total)

    search(0, frozenset(), 0.0)
    return best


def win_probability(
    my_contexts: list[PlayerContext],
    opponent_contexts: list[PlayerContext],
    draws: int,
    rng: np.random.Generator,
) -> tuple[float, tuple[float, float]]:
    """P(my total > opponent total), plus an honest uncertainty band.

    The band is not the Monte Carlo standard error -- that would be dishonestly narrow. It
    reflects the fact that the *inputs* are weak: projections explain 3-23% of variance, and
    the opponent's players are exactly as uncertain as ours. A stated 63% should be read as
    roughly 55-70%. See fantasy-decision-math section 4.
    """
    mine = simulate(my_contexts, draws, rng).sum(axis=1)
    theirs = simulate(opponent_contexts, draws, rng).sum(axis=1)
    p = float((mine > theirs).mean())
    # Widen toward 50% by a fixed fraction of the distance -- input uncertainty pulls any
    # confident-looking number back toward a coin flip.
    spread = 0.35 * abs(p - 0.5) + 0.05
    return p, (max(0.0, p - spread), min(1.0, p + spread))


def contested_slots(
    candidates: list[Candidate],
    settings: LeagueSettings,
    max_slots: int = 3,
) -> list[tuple[Slot, PlayerId, PlayerId]]:
    """Find the slots where the choice is genuinely close.

    Returns ``(slot, starter, best_alternative)``. Everything else is settled and does not
    need to be shown to a human or simulated.
    """
    assignment = greedy_lineup(candidates, settings)
    by_id = {c.id: c for c in candidates}
    benched = [c for c in candidates if c.id not in assignment]
    out: list[tuple[Slot, PlayerId, PlayerId, float]] = []

    for pid, slot in assignment.items():
        starter = by_id[pid]
        rivals = [
            b
            for b in benched
            if b.player.can_fill(slot)
            and abs(b.projection.mean - starter.projection.mean) < NOISE_THRESHOLD_POINTS * 2
        ]
        if not rivals:
            continue
        best = max(rivals, key=lambda b: b.projection.mean)
        gap = abs(best.projection.mean - starter.projection.mean)
        out.append((slot, pid, best.id, gap))

    out.sort(key=lambda t: t[3])
    return [(s, a, b) for s, a, b, _ in out[:max_slots]]


def enumerate_candidate_lineups(
    candidates: list[Candidate],
    settings: LeagueSettings,
    max_swaps: int = 2,
) -> list[dict[PlayerId, Slot]]:
    """The handful of lineups worth simulating: the greedy one plus close swaps.

    This is the whole "optimization" step. The combinatorics are trivial; the statistics
    are everything.
    """
    base = greedy_lineup(candidates, settings)
    lineups = [base]
    swaps = contested_slots(candidates, settings, max_slots=max_swaps + 1)

    for r in range(1, max_swaps + 1):
        for combo in combinations(swaps, r):
            variant = dict(base)
            ok = True
            for slot, starter, alternative in combo:
                if variant.get(starter) != slot:
                    ok = False
                    break
                del variant[starter]
                variant[alternative] = slot
            if ok and variant not in lineups:
                lineups.append(variant)
    return lineups


def decide(
    slot: Slot,
    starter: PlayerId,
    alternative: PlayerId,
    win_prob_delta: float,
    starter_proj: Projection,
    alt_proj: Projection,
    am_i_favored: bool,
) -> SlotDecision:
    """Turn a simulated delta into a verdict a human can act on, or an honest shrug.

    The tail logic: as a favorite you are evaluating the survival function at thresholds
    almost anyone clears, so the left tail separates candidates -- take the floor. As an
    underdog only the right tail matters -- take the ceiling even at a lower mean. In a
    close matchup expected points is the right answer, which is most weeks.
    """
    points_gap = abs(starter_proj.mean - alt_proj.mean)

    # Both gates must agree before we call it a tie. The simulation is the arbiter: a small
    # mean gap with a meaningful win-probability delta is a real variance play, not noise,
    # and collapsing it to "too close" would throw away the only edge the tails offer.
    if abs(win_prob_delta) < NOISE_THRESHOLD_WIN_PROB and points_gap < NOISE_THRESHOLD_POINTS:
        return SlotDecision(
            slot=slot,
            winner=starter,
            runner_up=alternative,
            win_prob_delta=win_prob_delta,
            confidence=Confidence.TOO_CLOSE,
            reason=(
                f"{points_gap:.1f} point gap is inside the noise "
                f"(weekly projection error is about 5 points) and the simulation cannot "
                f"separate them. Either is fine; keeping current."
            ),
        )

    winner, loser = (alternative, starter) if win_prob_delta > 0 else (starter, alternative)
    w_proj, l_proj = (alt_proj, starter_proj) if win_prob_delta > 0 else (starter_proj, alt_proj)

    if am_i_favored and w_proj.total_sd < l_proj.total_sd:
        why = "you are favored, so the safer floor is worth more than the upside"
    elif not am_i_favored and w_proj.total_sd > l_proj.total_sd:
        why = "you are an underdog, so the higher ceiling is worth the extra risk"
    else:
        why = f"higher projection ({w_proj.mean:.1f} vs {l_proj.mean:.1f})"

    confidence = Confidence.CLEAR if abs(win_prob_delta) > 0.02 else Confidence.LEAN
    return SlotDecision(
        slot=slot,
        winner=winner,
        runner_up=loser,
        win_prob_delta=win_prob_delta,
        confidence=confidence,
        reason=f"{why}; worth {abs(win_prob_delta) * 100:.1f}% win probability",
    )
