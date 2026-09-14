"""The shape a lineup recommendation takes when it is returned and when it is stored.

One function, two callers: the ``ff_recommend_lineup`` tool and the Sunday scheduler job.
They are the only two things in this app that produce a lineup recommendation, and BUG-013
is what happens when they each shape their own: the tool wrote objects, the job wrote
tuples, nothing compared them, and calibration silently graded half the season. So the
shaping lives here rather than in either caller.

This is `api/` rather than `services/` on purpose. It is serialization, which CLAUDE.md 2.1
puts on this side of the line, and `services/week.py` cannot host it without `recommend.py`
and `week.py` importing each other.

**Every player appears twice: once by name and once by id.** The name is for the human who
reads the answer, the id is for the calibration that grades it. Storing only names would
make the record ungradeable -- outcomes are keyed by player id and two players can share a
name -- and storing only ids would make the answer unreadable. The duplication is small and
the alternative is losing one of the two audiences.
"""

from __future__ import annotations

from typing import Any

from ff.domain.models import Recommendation
from ff.services.week import WeekBundle


def lineup_plan(result: Recommendation, bundle: WeekBundle, week: int) -> dict[str, Any]:
    """The full recommendation payload, returned to Claude and persisted verbatim."""
    by_id = {str(p.id): p for p in bundle.roster.players}

    def name(player_id: Any) -> str:
        key = str(player_id)
        player = by_id.get(key)
        return player.name if player else key

    return {
        "week": week,
        "win_probability": round(result.lineup.win_probability, 4),
        "win_probability_band": [round(x, 4) for x in result.lineup.win_probability_band],
        "expected_points": round(result.lineup.expected_points, 2),
        "starters": [
            {
                "slot": slot.value,
                "player": name(pid),
                "player_id": str(pid),
                "projection": round(bundle.projections[pid].mean, 2)
                if pid in bundle.projections
                else None,
            }
            for pid, slot in result.lineup.assignments
        ],
        "contested_slots": [
            {
                "slot": decision.slot.value,
                "start": name(decision.winner),
                "start_id": str(decision.winner),
                "over": name(decision.runner_up) if decision.runner_up else None,
                # None rather than the string "None" when a slot had no real alternative.
                # Calibration skips those, and "None" would look like a player id.
                "over_id": str(decision.runner_up) if decision.runner_up else None,
                "win_probability_delta": round(decision.win_prob_delta, 4),
                "confidence": decision.confidence.value,
                "reason": decision.reason,
            }
            for decision in result.decisions
        ],
        "sources": [
            {
                "name": s.name,
                "ok": s.ok,
                "required": s.required,
                "age_seconds": None if s.age_seconds is None else round(s.age_seconds),
                "detail": s.detail,
            }
            for s in bundle.sources
        ],
        "unprojected": [name(pid) for pid in bundle.unprojected],
        "caveats": list(result.caveats) + list(bundle.notes),
    }


def lineup_reasoning(result: Recommendation) -> str:
    return (" ".join(result.lineup.notes) + " " + " ".join(result.caveats)).strip()
