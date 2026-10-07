"""Usage from nflverse, and the profile built from it.

The fixtures are real: every 2026 week 1-4 row for ATL, DAL and SEA from
``load_player_stats`` and ``load_snap_counts``, recorded 2026-10-06. Whole teams, because
a carry share needs the team's total carries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ff.adapters.nflverse_usage import NflverseUsage, index_usage
from ff.core.cache import FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position, slots_for
from ff.domain.usage import UsageWeek, profile

FIXTURES = Path(__file__).parent / "fixtures" / "nflverse"
STATS: list[dict[str, Any]] = json.loads((FIXTURES / "usage_2026_weeks1to4.json").read_text())
SNAPS: list[dict[str, Any]] = json.loads((FIXTURES / "snaps_2026_weeks1to4.json").read_text())


def player(pid: str, name: str, position: Position, team: str) -> Player:
    return Player(
        id=PlayerId(pid),
        name=name,
        position=position,
        team=team,
        eligible_slots=slots_for(position),
    )


def week(n: int, **kw: Any) -> UsageWeek:
    base: dict[str, Any] = {
        "snap_share": 0.7,
        "targets": 5,
        "target_share": 0.2,
        "air_yards_share": 0.2,
        "carries": 0,
        "rush_share": None,
        "points": 10.0,
    }
    return UsageWeek(week=n, **{**base, **kw})


class TestIndex:
    def test_a_real_game_line(self) -> None:
        bijan = index_usage(STATS, SNAPS, 2026, 5)["RB:bijan robinson"]
        first = bijan[0]
        assert (first.week, first.carries, first.targets) == (1, 21, 10)
        assert first.snap_share == 0.77
        assert first.target_share == pytest.approx(0.526, abs=1e-3)

    def test_carry_share_is_against_the_whole_team(self) -> None:
        bijan = index_usage(STATS, SNAPS, 2026, 5)["RB:bijan robinson"]
        team = sum(r["carries"] or 0 for r in STATS if r["team"] == "ATL" and r["week"] == 1)
        assert bijan[0].rush_share == pytest.approx(21 / team)

    def test_points_in_the_league_format(self) -> None:
        """CeeDee Lamb, week 4: 41.3 PPR on 17 catches is 32.8 in half-PPR."""
        lamb = index_usage(STATS, SNAPS, 2026, 5, "half_ppr")["WR:ceedee lamb"]
        assert [g.points for g in lamb if g.week == 4] == [pytest.approx(32.8)]

    def test_only_weeks_already_played(self) -> None:
        lamb = index_usage(STATS, SNAPS, 2026, 3)["WR:ceedee lamb"]
        assert [g.week for g in lamb] == [1, 2]

    def test_another_season_is_nothing(self) -> None:
        assert index_usage(STATS, SNAPS, 2025, 5) == {}


class TestProfile:
    def index(self) -> dict[str, list[UsageWeek]]:
        return index_usage(STATS, SNAPS, 2026, 5)

    def test_a_rising_target_share(self) -> None:
        """Lamb: 27% and 29% of targets in weeks 1-2, then 20% and 49%."""
        lamb = profile("CeeDee Lamb", Position.WR, self.index()["WR:ceedee lamb"])
        assert lamb is not None
        assert lamb.trend == "rising"
        assert lamb.role == "lead target"
        assert "CeeDee Lamb plays 81% of snaps, gets 31% of targets" in lamb.summary

    def test_a_falling_target_share(self) -> None:
        """Smith-Njigba: 46% and 42%, then 31% and 30%."""
        jsn = profile("Jaxon Smith-Njigba", Position.WR, self.index()["WR:jaxon smith njigba"])
        assert jsn is not None and jsn.trend == "falling"

    def test_a_back_is_judged_on_snaps(self) -> None:
        javonte = profile("Javonte Williams", Position.RB, self.index()["RB:javonte williams"])
        assert javonte is not None
        assert javonte.role == "workhorse"
        assert javonte.trend_measure == "snap share"
        assert javonte.trend == "steady"
        assert "carries and targets a game" in javonte.summary

    def test_tight_ends_have_their_own_bands(self) -> None:
        """Jake Ferguson: 10% of targets. Fringe for a receiver, part-time for a TE."""
        ferguson = profile("Jake Ferguson", Position.TE, self.index()["TE:jake ferguson"])
        assert ferguson is not None and ferguson.role == "part-time target"

    def test_a_recent_change_is_shown_next_to_the_season_average(self) -> None:
        """Zay Flowers, 2026 weeks 1, 3, 4: 29%, 33%, 73% of snaps."""
        games = [
            week(1, snap_share=0.29, target_share=0.25),
            week(3, snap_share=0.33, target_share=0.30),
            week(4, snap_share=0.73, target_share=0.34),
        ]
        flowers = profile("Zay Flowers", Position.WR, games)
        assert flowers is not None
        assert "plays 45% of snaps (73% last game)" in flowers.summary

    def test_kickers_and_defenses_have_no_usage(self) -> None:
        assert profile("K", Position.K, [week(1)]) is None
        assert profile("D", Position.DEF, [week(1)]) is None

    def test_no_games_is_none_not_an_empty_role(self) -> None:
        assert profile("Rookie", Position.WR, []) is None

    def test_two_games_is_too_few_for_a_trend(self) -> None:
        two = profile("A", Position.WR, [week(1), week(2, target_share=0.4)])
        assert two is not None and two.trend == "too few games"

    def test_a_small_shift_is_steady(self) -> None:
        games = [week(1), week(2), week(3, target_share=0.22), week(4, target_share=0.22)]
        assert profile("A", Position.WR, games).trend == "steady"  # type: ignore[union-attr]


def usage(tmp_path: Path, **kw: Any) -> NflverseUsage:
    return NflverseUsage(
        2026,
        FileCache(tmp_path / "cache"),
        scoring="half_ppr",
        stats_loader=kw.get("stats", lambda _: STATS),
        snaps_loader=kw.get("snaps", lambda _: SNAPS),
    )


class TestSource:
    def test_matches_any_league_player_by_name(self, tmp_path: Path) -> None:
        bijan = player("470.p.40890", "Bijan Robinson", Position.RB, "ATL")
        out = usage(tmp_path).weekly_usage(5, [bijan])
        assert [g.week for g in out[bijan.id]] == [1, 2, 3, 4]

    def test_an_unknown_player_is_absent(self, tmp_path: Path) -> None:
        nobody = player("x", "Nobody Atall", Position.WR, "ATL")
        assert usage(tmp_path).weekly_usage(5, [nobody]) == {}

    def test_a_loader_failure_degrades(self, tmp_path: Path) -> None:
        def boom(_: Any) -> Any:
            raise OSError("github is down")

        source = usage(tmp_path, stats=boom)
        with pytest.raises(SourceUnavailable) as caught:
            source.weekly_usage(5, [])
        assert caught.value.required is False
        assert source.status().ok is False

    def test_a_renamed_column_is_drift(self, tmp_path: Path) -> None:
        renamed = [
            {**{k: v for k, v in r.items() if k != "target_share"}, "tgt_share": 0} for r in STATS
        ]
        with pytest.raises(SchemaDrift, match="target_share"):
            usage(tmp_path, stats=lambda _: renamed).weekly_usage(5, [])

    def test_a_second_call_is_served_from_cache(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def counting(_: Any) -> Any:
            calls["n"] += 1
            return STATS

        source = usage(tmp_path, stats=counting)
        source.weekly_usage(5, [])
        source.weekly_usage(5, [])
        assert calls["n"] == 1
