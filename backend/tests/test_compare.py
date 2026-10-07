"""The comparison verdict, and the floor and ceiling it draws on."""

from __future__ import annotations

from ff.domain.compare import Option, compare
from ff.domain.distributions import floor_ceiling
from ff.domain.models import PlayerId, Projection


def test_a_gap_outside_the_noise_is_a_pick() -> None:
    verdict = compare([Option("Flowers", 14.0), Option("Burden", 9.5)])
    assert verdict.pick == "Flowers"
    assert verdict.too_close is False
    assert verdict.gap == 4.5
    assert verdict.ranked == ("Flowers", "Burden")


def test_a_gap_inside_the_noise_is_too_close_to_call() -> None:
    verdict = compare([Option("Flowers", 11.2), Option("Burden", 10.1)])
    assert verdict.pick is None
    assert verdict.too_close is True
    assert verdict.reason.startswith("Too close to call.")
    assert verdict.ranked == ("Flowers", "Burden"), "still ordered, never hidden"


def test_three_way_tie_names_everyone_inside_the_band() -> None:
    verdict = compare([Option("A", 12.0), Option("B", 11.0), Option("C", 10.5), Option("D", 6.0)])
    assert "A, B and C" in verdict.reason
    assert "D" not in verdict.reason.split("(")[0]


def test_usage_leans_only_when_exactly_one_role_is_growing() -> None:
    one = compare(
        [
            Option("A", 11.0, trend="steady", trend_measure="target share"),
            Option("B", 10.0, trend="rising", trend_measure="target share"),
        ]
    )
    assert one.lean is not None and one.lean.startswith("If you want a tiebreak: B's")
    assert "[theory]" in one.lean

    both = compare([Option("A", 11.0, trend="rising"), Option("B", 10.0, trend="rising")])
    assert both.lean is None


def test_no_usage_lean_when_the_call_is_clear() -> None:
    verdict = compare([Option("A", 15.0), Option("B", 9.0, trend="rising")])
    assert verdict.lean is None


def test_floor_and_ceiling_name_two_different_players() -> None:
    verdict = compare(
        [
            Option("Safe", 11.0, floor=7.0, ceiling=16.0),
            Option("Boom", 10.5, floor=3.0, ceiling=22.0),
        ]
    )
    assert verdict.floor_vs_ceiling is not None
    assert "Safe has the higher floor" in verdict.floor_vs_ceiling
    assert "Boom has the higher ceiling" in verdict.floor_vs_ceiling


def test_an_unprojected_player_is_listed_last_and_said_so() -> None:
    verdict = compare([Option("A", None), Option("B", 12.0), Option("C", 4.0)])
    assert verdict.ranked == ("B", "C", "A")
    assert "No projection for A" in verdict.reason


def test_nothing_projected_is_no_pick() -> None:
    verdict = compare([Option("A", None), Option("B", None)])
    assert verdict.pick is None and verdict.too_close is False


def test_floor_and_ceiling_bracket_the_mean() -> None:
    projection = Projection(PlayerId("1"), mean=12.0, epistemic_sd=2.0, aleatoric_sd=5.0)
    low, high = floor_ceiling(projection)
    assert 0.0 <= low < 12.0 < high
    assert high - low > 10.0, "a typical receiver's week spans more than ten points"


def test_a_wider_projection_has_a_lower_floor_and_higher_ceiling() -> None:
    narrow = Projection(PlayerId("1"), mean=12.0, epistemic_sd=1.0, aleatoric_sd=3.0)
    wide = Projection(PlayerId("2"), mean=12.0, epistemic_sd=1.0, aleatoric_sd=7.0)
    assert floor_ceiling(wide)[0] < floor_ceiling(narrow)[0]
    assert floor_ceiling(wide)[1] > floor_ceiling(narrow)[1]
