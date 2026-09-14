"""Tests for lineup assembly. The property test is the important one."""

from __future__ import annotations

import random

import numpy as np
import pytest

from ff.domain.distributions import PlayerContext, simulate
from ff.domain.lineup import (
    Candidate,
    brute_force_lineup,
    contested_slots,
    decide,
    greedy_lineup,
    win_probability,
)
from ff.domain.models import (
    Confidence,
    LeagueKey,
    LeagueSettings,
    Player,
    PlayerId,
    Position,
    Projection,
    Slot,
)

SETTINGS = LeagueSettings(
    league_key=LeagueKey("461.l.1"),
    slot_counts={
        Slot.QB: 1,
        Slot.RB: 2,
        Slot.WR: 2,
        Slot.TE: 1,
        Slot.FLEX: 1,
        Slot.K: 1,
        Slot.DEF: 1,
        Slot.BENCH: 6,
    },
    uses_faab=True,
    faab_budget=100,
    playoff_start_week=15,
    num_teams=12,
)

SLOTS_FOR = {
    Position.QB: {Slot.QB},
    Position.RB: {Slot.RB, Slot.FLEX},
    Position.WR: {Slot.WR, Slot.FLEX},
    Position.TE: {Slot.TE, Slot.FLEX},
    Position.K: {Slot.K},
    Position.DEF: {Slot.DEF},
}


def make(pid: str, pos: Position, mean: float, sd: float = 5.0) -> Candidate:
    player = Player(
        id=PlayerId(pid),
        name=pid,
        position=pos,
        team="XX",
        eligible_slots=frozenset(SLOTS_FOR[pos]),
    )
    return Candidate(
        player=player,
        projection=Projection(
            PlayerId(pid), mean, sd * 0.3, sd, per_source=(("a", mean), ("b", mean))
        ),
    )


def test_greedy_fills_every_slot_when_roster_is_full() -> None:
    candidates = [
        make("qb1", Position.QB, 20),
        make("rb1", Position.RB, 15),
        make("rb2", Position.RB, 12),
        make("rb3", Position.RB, 9),
        make("wr1", Position.WR, 14),
        make("wr2", Position.WR, 11),
        make("te1", Position.TE, 8),
        make("k1", Position.K, 7),
        make("def1", Position.DEF, 6),
    ]
    assignment = greedy_lineup(candidates, SETTINGS)
    assert len(assignment) == len(SETTINGS.starting_slots)
    # Best leftover RB/WR/TE takes the flex.
    assert assignment[PlayerId("rb3")] == Slot.FLEX


def test_greedy_never_double_books_a_player() -> None:
    candidates = [make(f"p{i}", Position.RB, 10 + i) for i in range(4)]
    assignment = greedy_lineup(candidates, SETTINGS)
    assert len(set(assignment)) == len(assignment)


@pytest.mark.parametrize("seed", range(25))
def test_greedy_is_optimal_on_random_rosters(seed: int) -> None:
    """The matroid claim, checked against exhaustive search.

    Eligibility is nested (FLEX contains RB/WR/TE), so greedy by projection is provably
    optimal. If this ever fails, the league added a constraint that breaks the matroid and
    the algorithm must change -- see docs/DECISIONS.md.
    """
    rng = random.Random(seed)
    candidates: list[Candidate] = []
    for pos, count in [
        (Position.QB, 2),
        (Position.RB, 4),
        (Position.WR, 4),
        (Position.TE, 2),
        (Position.K, 1),
        (Position.DEF, 1),
    ]:
        for i in range(count):
            candidates.append(make(f"{pos}{i}", pos, round(rng.uniform(2, 25), 2)))

    assignment = greedy_lineup(candidates, SETTINGS)
    by_id = {c.id: c for c in candidates}
    greedy_total = sum(by_id[p].projection.mean for p in assignment)
    assert greedy_total == pytest.approx(brute_force_lineup(candidates, SETTINGS), abs=1e-6)


def test_contested_slots_ignores_settled_calls() -> None:
    candidates = [
        make("qb1", Position.QB, 25),
        make("rb1", Position.RB, 18),
        make("rb2", Position.RB, 17),
        make("rb3", Position.RB, 16.8),  # contested with rb2 and the flex
        make("wr1", Position.WR, 14),
        make("wr2", Position.WR, 4),  # nobody close
        make("te1", Position.TE, 9),
        make("k1", Position.K, 7),
        make("def1", Position.DEF, 6),
    ]
    contested = contested_slots(candidates, SETTINGS)
    assert all(gap_slot in SETTINGS.starting_slots for gap_slot, _, _ in contested)
    assert PlayerId("wr2") not in [alt for _, _, alt in contested]


def test_too_close_to_call_is_a_real_answer() -> None:
    """A sub-2-point gap is inside the projection noise. Saying so is required."""
    a = Projection(PlayerId("a"), 12.0, 1.0, 4.0)
    b = Projection(PlayerId("b"), 11.4, 1.0, 4.0)
    d = decide(Slot.FLEX, PlayerId("a"), PlayerId("b"), 0.001, a, b, am_i_favored=True)
    assert d.confidence is Confidence.TOO_CLOSE
    assert "noise" in d.reason


def test_favorite_prefers_the_floor() -> None:
    steady = Projection(PlayerId("steady"), 12.0, 1.0, 3.0)
    volatile = Projection(PlayerId("volatile"), 15.0, 1.0, 12.0)
    d = decide(Slot.FLEX, PlayerId("volatile"), PlayerId("steady"), 0.03, volatile, steady, True)
    assert d.winner == PlayerId("steady")
    assert "favored" in d.reason


def test_underdog_prefers_the_ceiling() -> None:
    steady = Projection(PlayerId("steady"), 12.0, 1.0, 3.0)
    volatile = Projection(PlayerId("volatile"), 11.0, 1.0, 12.0)
    d = decide(Slot.FLEX, PlayerId("steady"), PlayerId("volatile"), 0.03, steady, volatile, False)
    assert d.winner == PlayerId("volatile")
    assert "underdog" in d.reason


def _ctx(pid: str, pos: Position, mean: float, sd: float, team: str = "AAA") -> PlayerContext:
    return PlayerContext(
        player_id=PlayerId(pid),
        position=pos,
        nfl_team=team,
        opponent_team="ZZZ",
        projection=Projection(PlayerId(pid), mean, sd * 0.3, sd),
    )


def test_win_probability_band_is_wider_than_monte_carlo_error() -> None:
    """The band reflects weak inputs, not sampling noise. It must be visibly wide."""
    rng = np.random.default_rng(7)
    mine = [_ctx(f"m{i}", Position.WR, 15, 6) for i in range(5)]
    theirs = [_ctx(f"t{i}", Position.WR, 12, 6, team="BBB") for i in range(5)]
    p, (low, high) = win_probability(mine, theirs, 4000, rng)
    assert p > 0.5
    assert high - low > 0.08


def test_simulate_respects_zero_inflation() -> None:
    rng = np.random.default_rng(3)
    ctx = PlayerContext(
        PlayerId("x"),
        Position.RB,
        "AAA",
        "BBB",
        Projection(PlayerId("x"), 10.0, 2.0, 5.0, p_zero=0.25),
    )
    draws = simulate([ctx], 5000, rng)
    zero_rate = float((draws[:, 0] == 0.0).mean())
    assert 0.20 < zero_rate < 0.30
    assert draws.min() >= 0.0
