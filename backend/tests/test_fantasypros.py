"""FantasyPros.

The 403 fixture is recorded. The success fixtures are not -- see
tests/fixtures/fantasypros/README.md. So these tests lean on what is knowable without a
key: that a wrong shape fails loudly and names the field, that an unmodelled position is
not an error, and that the rank std-dev is treated as ranks rather than points.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ff.adapters.fantasypros import (
    FantasyProsProjections,
    index_projections,
    index_ranks,
)
from ff.core.cache import FileCache
from ff.core.errors import ConfigError, SchemaDrift, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position, Slot

FIXTURES = Path(__file__).parent / "fixtures" / "fantasypros"


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


def source(tmp_path: Path, response: httpx.Response) -> FantasyProsProjections:
    return FantasyProsProjections(
        2026,
        "test-key",
        FileCache(tmp_path / "cache"),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: response)),
    )


class TestConfiguration:
    def test_no_key_fails_at_construction_with_the_price(self, tmp_path: Path) -> None:
        """The free tier is sample data. Finding that out mid-week is worse."""
        with pytest.raises(ConfigError, match="FF_FANTASYPROS_API_KEY"):
            FantasyProsProjections(2026, "", FileCache(tmp_path / "c"))

    def test_the_key_travels_in_the_header_not_the_url(self, tmp_path: Path) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            seen["url"] = str(request.url)
            return httpx.Response(200, json=load("projections_week1.json"))

        fp = FantasyProsProjections(
            2026,
            "secret-key",
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        fp.weekly(1, [])
        assert seen["headers"]["x-api-key"] == "secret-key"
        assert "secret-key" not in seen["url"]

    def test_fantasypros_is_a_required_source(self, tmp_path: Path) -> None:
        """It is paid for and it is the only source of the disagreement number."""
        assert source(tmp_path, httpx.Response(200, json={})).required is True


class TestTransport:
    def test_a_recorded_403_says_what_to_check(self, tmp_path: Path) -> None:
        fp = source(tmp_path, httpx.Response(403, json=load("malformed/no_api_key_403.json")))
        with pytest.raises(SourceUnavailable, match="403") as caught:
            fp.weekly(1, [])
        assert "subscription" in str(caught.value)
        assert caught.value.required is True

    def test_a_200_of_html_is_schema_drift(self, tmp_path: Path) -> None:
        fp = source(tmp_path, httpx.Response(200, text="<html>nope</html>"))
        with pytest.raises(SchemaDrift, match="not JSON"):
            fp.weekly(1, [])

    def test_a_second_call_is_served_from_cache(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=load("projections_week1.json"))

        fp = FantasyProsProjections(
            2026,
            "k",
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        fp.weekly(1, [])
        fp.weekly(1, [])
        assert calls["n"] == 1


class TestProjections:
    def test_reads_points_for_every_modelled_position(self) -> None:
        index = index_projections(load("projections_week1.json"))
        prefixes = {key.split(":")[0] for key in index}
        assert {"QB", "RB", "WR", "TE", "K", "DEF"} <= prefixes

    def test_an_idp_row_is_skipped_rather_than_raising(self) -> None:
        """An ALL-position pull contains linebackers. That is not a schema problem."""
        index = index_projections(load("projections_week1.json"))
        assert not any("warner" in key for key in index)
        assert len(index) == 24

    def test_a_missing_points_field_names_the_field(self) -> None:
        with pytest.raises(SchemaDrift, match="points"):
            index_projections(load("malformed/projections_without_points.json"))

    def test_a_points_value_that_is_not_a_number_raises(self) -> None:
        with pytest.raises(SchemaDrift, match="not a number"):
            index_projections(load("malformed/projections_points_not_a_number.json"))

    def test_an_empty_player_list_raises(self) -> None:
        with pytest.raises(SchemaDrift, match="players"):
            index_projections(load("malformed/empty_players.json"))

    def test_a_non_object_body_raises(self) -> None:
        with pytest.raises(SchemaDrift, match="not an object"):
            index_projections([{"points": 1}])


class TestRanks:
    def test_reads_the_disagreement_spread(self) -> None:
        index = index_ranks(load("consensus_rankings_week1.json"))
        bijan = index["RB:bijan robinson"]
        assert bijan.ecr == 1.0
        assert bijan.std_dev == 0.7
        assert bijan.best == 1.0
        assert bijan.worst == 3.0
        assert bijan.tier == 1

    def test_the_std_dev_is_in_rank_units_and_stays_that_way(self) -> None:
        """A rank SD of 18.2 is not 18 points of uncertainty. Nothing here converts it."""
        index = index_ranks(load("consensus_rankings_week1.json"))
        assert index["WR:luke mccaffrey"].std_dev == 18.2

    def test_experts_agree_more_about_the_top_of_the_board(self) -> None:
        """A sanity property of any real consensus pull, and of this fixture."""
        index = index_ranks(load("consensus_rankings_week1.json"))
        assert index["RB:bijan robinson"].std_dev < index["RB:tyrone tracy"].std_dev

    def test_a_missing_std_dev_raises_and_says_why_it_matters(self) -> None:
        with pytest.raises(SchemaDrift, match="rank_std"):
            index_ranks(load("malformed/ranks_without_std_dev.json"))

    def test_ranks_match_to_yahoo_player_ids(self, tmp_path: Path) -> None:
        fp = source(tmp_path, httpx.Response(200, json=load("consensus_rankings_week1.json")))
        out = fp.ranks(1, [player("470.p.30977", "Bijan Robinson", Position.RB, "ATL")])
        assert out[PlayerId("470.p.30977")].std_dev == 0.7


class TestMatching:
    def test_matches_across_id_systems(self, tmp_path: Path) -> None:
        fp = source(tmp_path, httpx.Response(200, json=load("projections_week1.json")))
        out = fp.weekly(
            1,
            [
                player("470.p.30977", "Bijan Robinson", Position.RB, "ATL"),
                player("470.p.31881", "Jayden Daniels", Position.QB, "WAS"),
            ],
        )
        assert out == {PlayerId("470.p.30977"): 17.8, PlayerId("470.p.31881"): 21.9}

    def test_team_abbreviations_are_canonicalized(self, tmp_path: Path) -> None:
        """FantasyPros says WSH and JAC; Yahoo says WAS and JAX. Defenses join on team."""
        fp = source(tmp_path, httpx.Response(200, json=load("projections_week1.json")))
        out = fp.weekly(1, [player("470.p.41012", "Luke McCaffrey", Position.WR, "WAS")])
        assert PlayerId("470.p.41012") in out

    def test_suffixes_do_not_block_a_match(self, tmp_path: Path) -> None:
        fp = source(tmp_path, httpx.Response(200, json=load("projections_week1.json")))
        out = fp.weekly(1, [player("470.p.31455", "Tyrone Tracy", Position.RB, "NYG")])
        assert out == {PlayerId("470.p.31455"): 9.3}

    def test_defenses_join_on_team(self, tmp_path: Path) -> None:
        fp = source(tmp_path, httpx.Response(200, json=load("projections_week1.json")))
        out = fp.weekly(1, [player("470.p.100024", "Denver Broncos", Position.DEF, "DEN")])
        assert out == {PlayerId("470.p.100024"): 7.9}
