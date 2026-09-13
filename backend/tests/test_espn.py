"""ESPN projections.

The fixture is recorded from the live endpoint on 2026-09-12 and trimmed to the players
the Yahoo fixtures use. Most of these tests are about what happens when ESPN changes,
because it will, without notice, and the dangerous failure is not an exception -- it is a
200 that parses to nothing and leaves the ensemble running on one source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ff.adapters.espn import EspnProjections, index_by_match_key, validate, weekly_projection
from ff.core.cache import FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position, Slot

FIXTURES = Path(__file__).parent / "fixtures" / "espn"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def player(pid: str, name: str, position: Position, team: str) -> Player:
    return Player(
        id=PlayerId(pid),
        name=name,
        position=position,
        team=team,
        eligible_slots=frozenset({Slot(position.value)}),
    )


def source(tmp_path: Path, response: httpx.Response) -> EspnProjections:
    return EspnProjections(
        2026,
        FileCache(tmp_path / "cache"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: response)),
    )


class TestRequest:
    def test_limit_is_sent_with_a_sort(self, tmp_path: Path) -> None:
        """A bare limit is a 400: "Limit request must be accompanied by a sort"."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json=load("kona_player_info.json"))

        espn = EspnProjections(
            2026,
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        espn.weekly(1, [])
        header = json.loads(seen["x-fantasy-filter"])
        assert "limit" in header["players"]
        assert "sortPercOwned" in header["players"]

    def test_a_400_degrades_rather_than_raising_something_untyped(self, tmp_path: Path) -> None:
        body = load("malformed/bare_limit_400.json")
        espn = source(tmp_path, httpx.Response(400, json=body))
        with pytest.raises(SourceUnavailable) as caught:
            espn.weekly(1, [])
        assert caught.value.required is False
        assert "HTTP 400" in str(caught.value)

    def test_espn_is_never_a_required_source(self, tmp_path: Path) -> None:
        """It is an unversioned scrape. It does not get to fail a recommendation."""
        assert source(tmp_path, httpx.Response(200, json={})).required is False

    def test_a_200_of_html_is_schema_drift(self, tmp_path: Path) -> None:
        espn = source(tmp_path, httpx.Response(200, text="<html>blocked</html>"))
        with pytest.raises(SchemaDrift, match="not JSON"):
            espn.weekly(1, [])

    def test_a_second_call_is_served_from_cache(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=load("kona_player_info.json"))

        espn = EspnProjections(
            2026,
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        espn.weekly(1, [])
        espn.weekly(2, [])
        assert calls["n"] == 1


class TestValidation:
    """Validated on every fetch, not at startup -- docs/DATA_SOURCES.md rule 2."""

    def test_the_live_shape_passes(self) -> None:
        validate(load("kona_player_info.json"))

    def test_a_200_with_no_projections_is_caught(self) -> None:
        """This is the quiet one: it parses, it just silently yields nothing."""
        with pytest.raises(SchemaDrift, match="statSourceId"):
            validate(load("malformed/no_projections.json"))

    def test_an_empty_player_list_is_caught(self) -> None:
        with pytest.raises(SchemaDrift, match="players"):
            validate(load("malformed/empty_players.json"))

    def test_a_renamed_field_is_caught(self) -> None:
        with pytest.raises(SchemaDrift, match="fullName"):
            validate(load("malformed/renamed_name_field.json"))

    def test_a_retyped_field_is_caught(self) -> None:
        with pytest.raises(SchemaDrift, match="stats"):
            validate(load("malformed/stats_not_a_list.json"))

    def test_a_non_object_body_is_caught(self) -> None:
        with pytest.raises(SchemaDrift, match="not an object"):
            validate([1, 2, 3])


class TestDecoding:
    def test_reads_the_projection_not_the_actual(self) -> None:
        """statSourceId 0 is what happened; 1 is what ESPN thinks will happen."""
        stats = [
            {
                "statSourceId": 0,
                "statSplitTypeId": 1,
                "scoringPeriodId": 1,
                "seasonId": 2026,
                "appliedTotal": 31.4,
            },
            {
                "statSourceId": 1,
                "statSplitTypeId": 1,
                "scoringPeriodId": 1,
                "seasonId": 2026,
                "appliedTotal": 18.2,
            },
        ]
        assert weekly_projection(stats, 2026, 1) == 18.2

    def test_ignores_the_season_total(self) -> None:
        stats = [
            {
                "statSourceId": 1,
                "statSplitTypeId": 0,
                "scoringPeriodId": 0,
                "seasonId": 2026,
                "appliedTotal": 372.1,
            },
        ]
        assert weekly_projection(stats, 2026, 1) is None

    def test_last_seasons_week_two_is_not_this_seasons(self) -> None:
        """The same response carries both, under the same scoringPeriodId."""
        stats = [
            {
                "statSourceId": 1,
                "statSplitTypeId": 1,
                "scoringPeriodId": 2,
                "seasonId": 2025,
                "appliedTotal": 18.0,
            },
            {
                "statSourceId": 1,
                "statSplitTypeId": 1,
                "scoringPeriodId": 2,
                "seasonId": 2026,
                "appliedTotal": 22.6,
            },
        ]
        assert weekly_projection(stats, 2026, 2) == 22.6
        assert weekly_projection(stats, 2025, 2) == 18.0

    def test_a_week_with_no_row_returns_none(self) -> None:
        assert weekly_projection([], 2026, 1) is None


class TestMatching:
    def test_matches_players_across_id_systems(self, tmp_path: Path) -> None:
        espn = source(tmp_path, httpx.Response(200, json=load("kona_player_info.json")))
        out = espn.weekly(
            1,
            [
                player("470.p.30977", "Bijan Robinson", Position.RB, "ATL"),
                player("470.p.30123", "Amon-Ra St. Brown", Position.WR, "DET"),
            ],
        )
        assert set(out) == {PlayerId("470.p.30977"), PlayerId("470.p.30123")}
        assert all(v > 0 for v in out.values())

    def test_punctuation_and_suffixes_do_not_block_a_match(self, tmp_path: Path) -> None:
        """Yahoo says "Tyrone Tracy", ESPN says "Tyrone Tracy Jr."."""
        espn = source(tmp_path, httpx.Response(200, json=load("kona_player_info.json")))
        out = espn.weekly(1, [player("470.p.31455", "Tyrone Tracy", Position.RB, "NYG")])
        assert PlayerId("470.p.31455") in out

    def test_defenses_match_on_team_not_name(self, tmp_path: Path) -> None:
        """Yahoo says "Denver Broncos"; ESPN says "Broncos D/ST"."""
        espn = source(tmp_path, httpx.Response(200, json=load("kona_player_info.json")))
        out = espn.weekly(1, [player("470.p.100024", "Denver Broncos", Position.DEF, "DEN")])
        assert PlayerId("470.p.100024") in out

    def test_a_player_espn_does_not_cover_is_absent_not_an_error(self, tmp_path: Path) -> None:
        """Luke McCaffrey is outside ESPN's top 300 by ownership. That is a thinner
        ensemble for him, not a failed fetch."""
        espn = source(tmp_path, httpx.Response(200, json=load("kona_player_info.json")))
        out = espn.weekly(1, [player("470.p.41012", "Luke McCaffrey", Position.WR, "WAS")])
        assert out == {}

    def test_the_index_covers_every_position(self) -> None:
        index = index_by_match_key(load("kona_player_info.json"), 2026, 1)
        prefixes = {key.split(":")[0] for key in index}
        assert {"QB", "RB", "WR", "TE", "K", "DEF"} <= prefixes


class TestStatus:
    def test_status_is_ok_after_a_good_fetch(self, tmp_path: Path) -> None:
        espn = source(tmp_path, httpx.Response(200, json=load("kona_player_info.json")))
        espn.weekly(1, [])
        status = espn.status()
        assert status.ok is True
        assert status.required is False
        assert status.age_seconds is not None

    def test_status_carries_the_reason_after_a_failure(self, tmp_path: Path) -> None:
        espn = source(tmp_path, httpx.Response(503, text="down"))
        with pytest.raises(SourceUnavailable):
            espn.weekly(1, [])
        status = espn.status()
        assert status.ok is False
        assert status.detail is not None and "503" in status.detail


class TestZeroIsNotAProjection:
    """BUG-007. ESPN renders "no projection" as appliedTotal 0.0 with an empty stat line.

    Found by comparing the two sources live rather than by reading either one: 22.4% of
    ESPN's keys came back as exactly 0.00, and the biggest "disagreements" with Sleeper
    turned out to be players ESPN had no number for at all -- Brock Bowers, listed OUT,
    at 0.00 against Sleeper's 15.14. Averaging those gives 7.57, a number that is wrong in
    both directions and would lose a start/sit call outright.

    The distinction that matters: a zero WITH a stat line is a real forecast (a backup who
    will not touch the ball). A zero with no stat line is a gap. The fixture holds both,
    recorded live on 2026-09-13.
    """

    def test_a_zero_with_no_stat_line_is_not_a_projection(self) -> None:
        payload = load("zero_means_no_projection.json")
        player = payload["players"][0]["player"]
        assert weekly_projection(player["stats"], 2026, 2) is None

    def test_a_real_projection_in_the_same_player_still_reads(self) -> None:
        """Proves the skip is about the empty stat line, not about the player."""
        payload = load("zero_means_no_projection.json")
        player = payload["players"][0]["player"]
        assert weekly_projection(player["stats"], 2026, 3) == pytest.approx(14.75, abs=0.01)

    def test_a_zero_with_a_stat_line_is_kept(self) -> None:
        """A genuine forecast of zero is information and must survive."""
        stats = [
            {
                "statSourceId": 1,
                "statSplitTypeId": 1,
                "scoringPeriodId": 2,
                "seasonId": 2026,
                "appliedTotal": 0.0,
                "stats": {"53": 0.0, "42": 0.0},
            }
        ]
        assert weekly_projection(stats, 2026, 2) == 0.0

    def test_the_gap_is_absent_from_the_index_not_zero_in_it(self) -> None:
        payload = load("zero_means_no_projection.json")
        assert index_by_match_key(payload, 2026, 2) == {}
        assert index_by_match_key(payload, 2026, 3) != {}
