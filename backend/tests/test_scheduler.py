"""The weekly jobs: when they fire, and that they compute without writing to Yahoo."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fakes import FIXTURE_WEEK, FixtureLeague, fixture_sources

from ff.adapters.store import Store
from ff.api.deps import Deps
from ff.api.scheduler import (
    LINEUP_JOB,
    PACIFIC,
    WAIVER_JOB,
    next_run,
    snapshot_lineup,
    snapshot_waivers,
)
from ff.core.config import Settings


class FixtureSleeper:
    def current_week(self) -> int:
        return FIXTURE_WEEK


def build(tmp_path: Path) -> Deps:
    config = Settings(
        yahoo_league_key="470.l.1000",
        yahoo_team_key="470.l.1000.t.3",
        database_path=str(tmp_path / "ff.db"),
        monte_carlo_draws=200,
        random_seed=3,
    )
    return Deps(
        yahoo=FixtureLeague(),
        sources=list(fixture_sources()),
        store=Store(config.database_path),
        sleeper=FixtureSleeper(),  # type: ignore[arg-type]
        config=config,
    )


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=PACIFIC)


def test_the_waiver_job_lands_on_tuesday_evening() -> None:
    """Tuesday 20:00 Pacific, before waivers process overnight. After they process the
    pool is a different pool and the snapshot is of nothing."""
    fires = next_run(*WAIVER_JOB, at("2026-09-13T08:00"))  # a Sunday
    assert (fires.weekday(), fires.hour) == (1, 20)
    assert fires.date().isoformat() == "2026-09-15"


def test_the_lineup_job_lands_on_sunday_morning() -> None:
    fires = next_run(*LINEUP_JOB, at("2026-09-15T21:00"))  # Tuesday, after the waiver job
    assert (fires.weekday(), fires.hour) == (6, 9)
    assert fires.date().isoformat() == "2026-09-20"


def test_a_job_due_this_minute_waits_a_week_rather_than_firing_twice() -> None:
    """Otherwise the loop re-arms at the same instant and runs the job in a tight circle."""
    assert next_run(6, 9, at("2026-09-13T09:00")).date().isoformat() == "2026-09-20"


def test_one_second_before_is_still_today() -> None:
    assert next_run(6, 9, at("2026-09-13T08:59:59")).date().isoformat() == "2026-09-13"


def test_the_lineup_job_persists_a_snapshot_and_a_recommendation(tmp_path: Path) -> None:
    deps = build(tmp_path)
    rec_id = snapshot_lineup(deps)
    assert [r.id for r in deps.store.list_recommendations(week=FIXTURE_WEEK)] == [rec_id]
    assert deps.store.latest_snapshot(FIXTURE_WEEK, "lineup") is not None


def test_the_waiver_job_persists_a_board_and_the_opponent_model(tmp_path: Path) -> None:
    deps = build(tmp_path)
    snapshot_waivers(deps)
    assert deps.store.latest_snapshot(FIXTURE_WEEK, "waivers") is not None
    assert deps.store.load_opponent_model("470.l.1000")


def test_neither_job_writes_to_yahoo(tmp_path: Path) -> None:
    """The jobs prepare; the owner decides. CLAUDE.md 5.1 holds on a timer too."""
    deps = build(tmp_path)
    snapshot_lineup(deps)
    snapshot_waivers(deps)
    assert deps.store.unfinished_writes() == []
    league = deps.yahoo
    assert isinstance(league, FixtureLeague)
    assert all(not call.startswith("submit") for call in league.calls)
