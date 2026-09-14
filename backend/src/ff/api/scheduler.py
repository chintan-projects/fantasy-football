"""The two jobs that have to run whether or not anyone asks.

MCP is request-driven. Nothing in this server wakes up on its own, so moving to MCP did
not remove the need for a scheduler -- it removed the only thing that was pretending to be
one. Two jobs matter:

* **Tuesday 20:00 Pacific.** Waivers clear overnight. Snapshot the board and the bid
  reasoning *before* they process, because afterwards the free agent pool is a different
  pool and there is nothing left to have been right or wrong about.
* **Sunday 09:00 Pacific.** Snapshot the lineup inputs while they are still actionable.
  An hour later the early games have started and half the roster is locked.
* **Tuesday 06:00 Pacific.** Ingest what everyone actually scored. After Monday Night
  Football, so the week is final, and before the Tuesday evening waiver snapshot.

Neither job notifies anybody and neither writes to Yahoo. What they do is capture the
inputs at the moment the decision was live, which is the only way M6 can ever score a
recommendation (CLAUDE.md 2.5). Asking at 3pm and grading against a 9am world would be
measuring the wrong thing.

It runs in-process rather than as its own machine because a Fly volume attaches to exactly
one machine and this app's database is on that volume. A second machine could not read it.
`[empirical]` -- Fly's volumes documentation, checked 2026-09-13.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ff.api.deps import Deps
from ff.api.payloads import lineup_plan, lineup_reasoning
from ff.core.logging import get_logger
from ff.services import calibration
from ff.services import waivers as waiver_service
from ff.services.recommend import recommend
from ff.services.week import load_week, to_week_inputs

log = get_logger(__name__)

PACIFIC = ZoneInfo("US/Pacific")

#: (weekday, hour) in Pacific. Monday is 0, so 1 is Tuesday and 6 is Sunday.
WAIVER_JOB = (1, 20)
LINEUP_JOB = (6, 9)
ACTUALS_JOB = (1, 6)


def next_run(weekday: int, hour: int, now: datetime) -> datetime:
    """The next time this weekday and hour comes round, in Pacific.

    Pure and separately tested, because "it fires at the right time" is not something a
    running server tells you until the week it does not.
    """
    local = now.astimezone(PACIFIC)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    days = (weekday - target.weekday()) % 7
    target += timedelta(days=days)
    if target <= local:
        target += timedelta(days=7)
    return target


def snapshot_lineup(deps: Deps) -> str:
    """Compute and persist a lineup recommendation. Writes nothing to Yahoo."""
    week = deps.sleeper.current_week()
    bundle = load_week(deps.yahoo, deps.sources, week)
    snapshot_id = deps.store.save_snapshot(week, "lineup", bundle.snapshot())
    result = recommend(
        to_week_inputs(bundle, draws=deps.config.monte_carlo_draws, seed=deps.config.random_seed)
    )
    # The same payload the tool returns, built by the same function. Shaping it here
    # independently is what produced BUG-013.
    return deps.store.save_recommendation(
        week,
        "lineup",
        lineup_plan(result, bundle, week),
        lineup_reasoning(result),
        snapshot_id=snapshot_id,
    )


def snapshot_waivers(deps: Deps) -> str:
    """Compute and persist the waiver board, and refresh the opponent model."""
    week = deps.sleeper.current_week()
    bundle = load_week(deps.yahoo, deps.sources, week, with_opponent=False)
    snapshot_id = deps.store.save_snapshot(week, "waivers", bundle.snapshot())
    waiver_service.refresh_opponent_model(
        deps.yahoo, deps.store, deps.my_team_key, bundle.settings.num_teams
    )
    board = waiver_service.build_board(deps.yahoo, deps.sources, bundle, limit=10)
    return deps.store.save_recommendation(
        week,
        "waivers",
        {
            "targets": [
                {
                    "player": e.target.player.name,
                    "vorp_per_week": e.target.ros_vorp_per_week,
                }
                for e in board
            ]
        },
        "Snapshot taken before waivers processed.",
        snapshot_id=snapshot_id,
    )


def ingest_actuals(deps: Deps) -> int:
    """Record what the players we projected actually scored. Returns the rows written.

    Both the current week and the one before it, because the NFL week rolls over somewhere
    around Tuesday and which side of it a 6am job lands on is not worth depending on.
    ``ingest`` upserts, so the redundant pass costs one fetch from a cached file and
    protects against the off-by-one that would otherwise lose a week permanently.

    Does nothing when no actuals source is configured, which is the case in tests.
    """
    if deps.actuals is None:
        log.info("actuals_ingest_skipped", reason="no actuals source configured")
        return 0
    week = deps.sleeper.current_week()
    written = 0
    for target in (week - 1, week):
        if target >= 1:
            written += calibration.ingest(deps.store, deps.actuals, target)
    return written


async def _run_weekly(
    name: str,
    weekday: int,
    hour: int,
    job: Callable[[], object],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        now = datetime.now(PACIFIC)
        wait = (next_run(weekday, hour, now) - now).total_seconds()
        log.info("job_scheduled", job=name, in_seconds=round(wait))
        await sleep(wait)
        try:
            # Off the event loop: a Monte Carlo run takes seconds and the server has to
            # stay answerable while it happens.
            await asyncio.to_thread(job)
            log.info("job_done", job=name)
        except Exception as exc:  # a failed job must not take the server down with it
            log.error("job_failed", job=name, error=str(exc))


def start(deps: Deps) -> list[asyncio.Task[None]]:
    """Start both jobs. Returns the tasks so a caller can cancel them on shutdown."""
    jobs = (
        ("waivers", WAIVER_JOB, lambda: snapshot_waivers(deps)),
        ("lineup", LINEUP_JOB, lambda: snapshot_lineup(deps)),
        ("actuals", ACTUALS_JOB, lambda: ingest_actuals(deps)),
    )
    return [
        asyncio.create_task(_run_weekly(name, weekday, hour, job))
        for name, (weekday, hour), job in jobs
    ]
