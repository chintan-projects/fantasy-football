"""Scoring the app against reality.

The thing most worth testing here is not that the arithmetic is right -- it is that the
module refuses to claim a winner it cannot support. CLAUDE.md section 3 makes "too close to
call" a required output, and a calibration report is exactly where a spurious ranking would
be most convincing and most wrong.
"""

from __future__ import annotations

from pathlib import Path

from ff.adapters.store import Store
from ff.domain.calibration import (
    MINIMUM_PAIRS,
    best_lineup,
    compare,
    score_decisions,
    score_lineup,
    score_sources,
)
from ff.domain.models import PlayerId, Slot
from ff.services import calibration

# ---- the honesty rules --------------------------------------------------------------


def forecasts(left: list[float], right: list[float]) -> dict[str, dict[PlayerId, float]]:
    return {
        "left": {PlayerId(str(i)): v for i, v in enumerate(left)},
        "right": {PlayerId(str(i)): v for i, v in enumerate(right)},
    }


def actuals(values: list[float]) -> dict[PlayerId, float]:
    return {PlayerId(str(i)): v for i, v in enumerate(values)}


def test_a_handful_of_weeks_never_separates_two_sources() -> None:
    """Five player-weeks cannot rank two forecasters, however lopsided they look."""
    truth = [10.0, 12.0, 8.0, 20.0, 5.0]
    result = compare(forecasts(truth, [v + 6.0 for v in truth]), actuals(truth), "left", "right")
    assert result is not None
    assert result.better == "left"
    assert not result.separated
    assert "Too few weeks" in result.verdict


def test_a_source_that_is_genuinely_better_is_eventually_called() -> None:
    """Noisy on purpose. A clean constant gap is the easy case and it is tested below."""
    truth = [10.0 + i % 7 for i in range(MINIMUM_PAIRS * 2)]
    sharp = [v + (0.4, -0.6, 1.1, -0.9)[i % 4] for i, v in enumerate(truth)]
    blunt = [v + (9.0, -7.5, 6.0, -8.5)[i % 4] for i, v in enumerate(truth)]
    result = compare(forecasts(sharp, blunt), actuals(truth), "left", "right")
    assert result is not None
    assert result.better == "left"
    assert result.separated
    assert "beats" in result.verdict


def test_a_gap_with_no_spread_is_the_strongest_evidence_not_the_weakest() -> None:
    """Found by a failing test, not by reading the code.

    When every paired difference is identical the standard error is zero, and the first
    version of ``separated`` read that as "no evidence" and printed "too close to call"
    for a forecaster that was worse by six points every single time. The degenerate case
    sat on the wrong side of the guard meant to catch degenerate cases.
    """
    truth = [10.0 + i % 7 for i in range(MINIMUM_PAIRS * 2)]
    result = compare(forecasts(truth, [v + 6.0 for v in truth]), actuals(truth), "left", "right")
    assert result is not None
    assert result.standard_error == 0.0
    assert result.separated
    assert result.better == "left"


def test_two_identical_forecasters_are_never_separated() -> None:
    truth = [10.0 + i % 7 for i in range(MINIMUM_PAIRS * 2)]
    result = compare(forecasts(truth, truth), actuals(truth), "left", "right")
    assert result is not None
    assert not result.separated
    assert "Identical" in result.verdict


def test_a_hard_week_hurts_both_sources_and_changes_nothing() -> None:
    """Why the comparison is paired.

    Every player underperforms by 8 points. Both forecasters are equally wrong about it, so
    the honest answer is that the week said nothing about which is better. An unpaired
    comparison would let that shared shock widen the error bar until nothing was ever
    detectable.
    """
    truth = [10.0 + i % 5 for i in range(MINIMUM_PAIRS * 2)]
    collapsed = [v - 8.0 for v in truth]
    result = compare(forecasts(truth, truth), actuals(collapsed), "left", "right")
    assert result is not None
    assert result.mae_difference == 0.0
    assert not result.separated


def test_bias_and_error_are_reported_separately() -> None:
    """A source that runs six points hot is fixable. A source that is noisy is not."""
    truth = [10.0, 12.0, 8.0, 20.0]
    scores = {
        s.source: s
        for s in score_sources(forecasts(truth, [v + 6.0 for v in truth]), actuals(truth))
    }
    assert scores["left"].mae == 0.0
    assert scores["right"].mae == 6.0
    assert scores["right"].bias == 6.0  # positive: it projects more than happens


def test_a_source_is_not_scored_on_players_it_never_projected() -> None:
    only_one = {"partial": {PlayerId("0"): 10.0}}
    scores = score_sources(only_one, actuals([10.0, 99.0, 99.0]))
    assert scores[0].n == 1


# ---- hindsight ----------------------------------------------------------------------

FLEX_ROSTER = {
    PlayerId("rb1"): frozenset({Slot.RB, Slot.FLEX}),
    PlayerId("rb2"): frozenset({Slot.RB, Slot.FLEX}),
    PlayerId("wr1"): frozenset({Slot.WR, Slot.FLEX}),
}


def test_the_scarce_slot_is_filled_before_the_flexible_one() -> None:
    """A FLEX filled first can strand the only body left for a required slot."""
    points = {PlayerId("rb1"): 20.0, PlayerId("rb2"): 5.0, PlayerId("wr1"): 18.0}
    filled = best_lineup(FLEX_ROSTER, points, (Slot.WR, Slot.FLEX))
    assert filled[PlayerId("wr1")] is Slot.WR
    assert filled[PlayerId("rb1")] is Slot.FLEX


def test_the_benchmark_is_what_the_owner_would_have_done_anyway() -> None:
    """The edge is measured against starting the highest projections, not against perfect.

    Hindsight-optimal is unreachable by anyone, so scoring against it would report a large
    loss every week regardless of decision quality and teach the owner nothing.
    """
    projections = {PlayerId("rb1"): 9.0, PlayerId("rb2"): 12.0, PlayerId("wr1"): 11.0}
    actual = {PlayerId("rb1"): 25.0, PlayerId("rb2"): 4.0, PlayerId("wr1"): 10.0}
    score = score_lineup(
        week=3,
        recommended={PlayerId("rb1"): Slot.RB, PlayerId("wr1"): Slot.WR},
        projections=projections,
        eligibility=FLEX_ROSTER,
        slots=(Slot.RB, Slot.WR),
        actual=actual,
    )
    assert score.recommended == 35.0
    assert score.baseline == 14.0  # rb2 by projection, plus wr1
    assert score.edge == 21.0
    assert score.best_possible == 35.0
    assert score.left_on_bench == 0.0


def test_a_starter_who_did_not_play_scores_zero_and_is_counted() -> None:
    """Counted, because a missing row and a name-matching failure look identical."""
    score = score_lineup(
        week=3,
        recommended={PlayerId("rb1"): Slot.RB, PlayerId("wr1"): Slot.WR},
        projections={PlayerId("rb1"): 9.0, PlayerId("wr1"): 11.0},
        eligibility=FLEX_ROSTER,
        slots=(Slot.RB, Slot.WR),
        actual={PlayerId("wr1"): 10.0},
    )
    assert score.recommended == 10.0
    assert score.missing_players == 1
    assert score.scored_players == 1


# ---- the service, against a real database -------------------------------------------


class FakeActuals:
    name = "fake_actuals"

    def __init__(self, points: dict[str, float]) -> None:
        self.points = points
        self.calls = 0

    def weekly(self, week: int, players: list) -> dict[PlayerId, float]:  # type: ignore[type-arg]
        self.calls += 1
        return {p.id: self.points[str(p.id)] for p in players if str(p.id) in self.points}


def snapshot(per_source: bool = True) -> dict[str, object]:
    projections: dict[str, object] = {
        "rb1": {"mean": 9.0, "epistemic_sd": 1.0, "aleatoric_sd": 4.0, "sources": ["a", "b"]},
        "rb2": {"mean": 12.0, "epistemic_sd": 1.0, "aleatoric_sd": 4.0, "sources": ["a", "b"]},
    }
    if per_source:
        projections["rb1"] = {**projections["rb1"], "per_source": {"a": 8.0, "b": 10.0}}  # type: ignore[dict-item]
        projections["rb2"] = {**projections["rb2"], "per_source": {"a": 13.0, "b": 11.0}}  # type: ignore[dict-item]
    return {
        "week": 2,
        "roster": [
            {
                "id": "rb1",
                "name": "One Back",
                "position": "RB",
                "team": "SF",
                "slots": ["RB", "W/R/T"],
            },
            {
                "id": "rb2",
                "name": "Two Back",
                "position": "RB",
                "team": "KC",
                "slots": ["RB", "W/R/T"],
            },
        ],
        "projections": projections,
    }


def test_ingest_records_only_the_players_the_snapshot_knew_about(tmp_path: Path) -> None:
    store = Store(tmp_path / "ff.db")
    store.save_snapshot(2, "lineup", snapshot())
    source = FakeActuals({"rb1": 22.0, "rb2": 3.0, "someone-else": 99.0})

    assert calibration.ingest(store, source, 2) == 2
    assert store.outcomes_for_week(2) == {"rb1": 22.0, "rb2": 3.0}


def test_ingest_is_safe_to_run_twice(tmp_path: Path) -> None:
    """Sunday's numbers are partial until Monday night. Re-running corrects them."""
    store = Store(tmp_path / "ff.db")
    store.save_snapshot(2, "lineup", snapshot())
    calibration.ingest(store, FakeActuals({"rb1": 4.0}), 2)
    calibration.ingest(store, FakeActuals({"rb1": 22.0}), 2)
    assert store.outcomes_for_week(2) == {"rb1": 22.0}


def test_a_week_with_no_snapshot_is_invisible_not_wrong(tmp_path: Path) -> None:
    store = Store(tmp_path / "ff.db")
    assert calibration.ingest(store, FakeActuals({"rb1": 22.0}), 9) == 0
    assert calibration.week_report(store, 9) is None


def test_a_week_with_no_outcomes_says_so_rather_than_scoring_zero(tmp_path: Path) -> None:
    store = Store(tmp_path / "ff.db")
    store.save_snapshot(2, "lineup", snapshot())
    report = calibration.week_report(store, 2)
    assert report is not None
    assert report.sources == []
    assert report.note is not None and "no outcomes yet" in report.note


def test_each_source_is_scored_on_what_it_actually_said(tmp_path: Path) -> None:
    store = Store(tmp_path / "ff.db")
    store.save_snapshot(2, "lineup", snapshot())
    calibration.ingest(store, FakeActuals({"rb1": 10.0, "rb2": 11.0}), 2)

    report = calibration.week_report(store, 2)
    assert report is not None
    scores = {s.source: s for s in report.sources}
    # a said 8 and 13 against 10 and 11; b said 10 and 11, which is exactly right.
    assert scores["b"].mae == 0.0
    assert scores["a"].mae == 2.0
    assert report.ensemble is not None and report.ensemble.mae == 1.0


def test_an_old_snapshot_without_per_source_numbers_is_unscorable_per_source(
    tmp_path: Path,
) -> None:
    """BUG-012's other half. The ensemble is still scorable; the sources are gone for good."""
    store = Store(tmp_path / "ff.db")
    store.save_snapshot(2, "lineup", snapshot(per_source=False))
    calibration.ingest(store, FakeActuals({"rb1": 10.0, "rb2": 11.0}), 2)

    report = calibration.week_report(store, 2)
    assert report is not None
    assert report.sources == []
    assert report.ensemble is not None


def test_pooling_weeks_does_not_let_one_week_overwrite_another(tmp_path: Path) -> None:
    """The same player id appears every week. Pooling without namespacing loses all but one.

    The failure is quiet and flattering: the sample stays at one week's size while the
    report claims a season, so the error bar looks the same and the confidence is fake.
    """
    store = Store(tmp_path / "ff.db")
    for week in (2, 3, 4):
        payload = {**snapshot(), "week": week}
        store.save_snapshot(week, "lineup", payload)
        calibration.ingest(store, FakeActuals({"rb1": 10.0, "rb2": 11.0}), week)

    season = calibration.season_report(store)
    assert season.weeks_scored == [2, 3, 4]
    assert {s.source: s.n for s in season.sources} == {"a": 6, "b": 6}


def test_the_season_report_grades_lineups_against_the_obvious_alternative(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "ff.db")
    snapshot_id = store.save_snapshot(2, "lineup", snapshot())
    store.save_recommendation(
        2,
        "lineup",
        {"win_probability": 0.55, "starters": [["rb1", "RB"]]},
        "because",
        snapshot_id=snapshot_id,
    )
    # rb2 had the higher projection, so the baseline starts him. He scored 3.
    calibration.ingest(store, FakeActuals({"rb1": 22.0, "rb2": 3.0}), 2)

    season = calibration.season_report(store)
    assert len(season.lineups) == 1
    assert season.lineups[0].edge == 19.0
    assert season.weeks_ahead_of_baseline == 1


# ---- individual start/sit calls -----------------------------------------------------

A, B, C, D = PlayerId("a"), PlayerId("b"), PlayerId("c"), PlayerId("d")


def test_a_call_it_refused_to_make_is_not_a_call_it_got_wrong() -> None:
    """The honesty property this whole record depends on.

    "Too close to call" is a real output (CLAUDE.md section 3). Grading it as a prediction
    would fill the record with coin flips -- and because the coins land both ways, it would
    make an honest model look mediocre and a reckless one look identical. The declined
    calls are counted and reported, never scored.
    """
    record = score_decisions(
        [(A, B, False), (C, D, False)],
        {A: 2.0, B: 30.0, C: 30.0, D: 2.0},
    )
    assert record.graded == 0
    assert record.declined == 2
    assert record.points_gained == 0.0
    assert record.right == 0 and record.wrong == 0
    assert "too close to call" in record.verdict


def test_the_record_reports_the_size_of_the_calls_not_just_the_count() -> None:
    """A 1-1 record hides the difference between +25 points and -25."""
    record = score_decisions(
        [(A, B, True), (C, D, True)],
        {A: 30.0, B: 5.0, C: 8.0, D: 9.0},
    )
    assert (record.right, record.wrong) == (1, 1)
    assert record.points_gained == 24.0  # +25 on the first, -1 on the second
    assert "+24.0 points" in record.verdict


def test_a_benched_player_who_never_played_is_skipped_not_scored_as_a_win() -> None:
    """Starting anyone over an inactive player is not a judgment the model made.

    Counting it would inflate the record with calls that were decided by the injury report
    rather than by the simulation.
    """
    record = score_decisions([(A, B, True)], {A: 14.0})
    assert record.graded == 0
    assert record.points_gained == 0.0


def test_a_slot_with_no_alternative_is_not_a_call() -> None:
    record = score_decisions([(A, None, True)], {A: 14.0})
    assert record.graded == 0
    assert record.declined == 0


def test_an_exact_tie_is_neither_right_nor_wrong() -> None:
    record = score_decisions([(A, B, True)], {A: 12.0, B: 12.0})
    assert (record.right, record.wrong, record.tied) == (0, 0, 1)
    assert record.graded == 1


def test_an_ungraded_record_says_so_rather_than_reporting_nothing() -> None:
    assert "No start/sit calls have been graded" in score_decisions([], {}).verdict
