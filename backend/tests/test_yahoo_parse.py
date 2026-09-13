"""Parsing Yahoo's JSON.

Fixtures are hand-built, not recorded -- Yahoo has no anonymous tier, so there is no token
on this machine to record against. See tests/fixtures/yahoo/README.md. The tests are
therefore written against structure and failure behaviour, which is the part that survives
a fixture being slightly wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ff.adapters.yahoo.parse import (
    find,
    merge,
    numbered,
    parse_game_id,
    parse_league_settings,
    parse_matchup,
    parse_players,
    parse_position,
    parse_roster,
    parse_slot,
)
from ff.core.errors import SchemaDrift
from ff.domain.models import Position, Slot

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class TestPrimitives:
    """The three shapes that make Yahoo's JSON hostile."""

    def test_merge_rebuilds_an_entity_split_across_single_key_dicts(self) -> None:
        assert merge([{"a": 1}, {"b": 2}, {"c": 3}]) == {"a": 1, "b": 2, "c": 3}

    def test_merge_flattens_the_nested_list_yahoo_wraps_players_in(self) -> None:
        assert merge([[{"a": 1}, {"b": 2}], {"c": 3}]) == {"a": 1, "b": 2, "c": 3}

    def test_merge_survives_the_empty_list_yahoo_wedges_in(self) -> None:
        assert merge([{"a": 1}, [], {"b": 2}]) == {"a": 1, "b": 2}

    def test_merge_keeps_the_entity_field_over_a_sub_resource_repeat(self) -> None:
        """Sub-resources come second and sometimes repeat a key. First value wins."""
        assert merge([{"week": "2"}, {"week": "99"}]) == {"week": "2"}

    def test_numbered_reads_the_pseudo_array_in_order(self) -> None:
        node = {"0": "a", "1": "b", "2": "c", "10": "k", "count": 4}
        assert numbered(node) == ["a", "b", "c", "k"]

    def test_numbered_ignores_the_count_sibling(self) -> None:
        assert numbered({"count": 3}) == []

    def test_find_walks_breadth_first_through_lists_and_dicts(self) -> None:
        assert find({"a": [{"b": {"c": 7}}]}, "c") == 7
        assert find({"a": 1}, "missing") is None


class TestPositionsAndSlots:
    def test_multi_position_player_takes_the_first_known(self) -> None:
        assert parse_position("RB,WR") is Position.RB

    def test_defense_aliases(self) -> None:
        assert parse_position("DST") is Position.DEF
        assert parse_position("DEF") is Position.DEF

    def test_flex_aliases_map_to_one_slot(self) -> None:
        assert parse_slot("W/R/T") is Slot.FLEX
        assert parse_slot("W/R") is Slot.FLEX

    def test_unknown_strings_return_none_rather_than_guessing(self) -> None:
        assert parse_position("ATH") is None
        assert parse_slot("OP") is None


class TestGameId:
    def test_reads_the_game_id_from_the_response(self) -> None:
        assert parse_game_id(load("game_nfl.json")) == "470"

    def test_missing_game_id_is_schema_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="game_id"):
            parse_game_id({"fantasy_content": {"game": [{"name": "Football"}]}})


class TestLeagueSettings:
    def test_reads_the_starting_lineup(self) -> None:
        settings = parse_league_settings(load("league_settings.json"))
        assert settings.slot_counts == {
            Slot.QB: 1,
            Slot.RB: 2,
            Slot.WR: 2,
            Slot.TE: 1,
            Slot.FLEX: 1,
            Slot.K: 1,
            Slot.DEF: 1,
        }

    def test_bench_and_ir_are_not_starting_slots(self) -> None:
        settings = parse_league_settings(load("league_settings.json"))
        assert Slot.BENCH not in settings.slot_counts
        assert Slot.IR not in settings.slot_counts
        assert len(settings.starting_slots) == 9

    def test_reads_uses_faab(self) -> None:
        """Checked before a bid is ever built: a priority-waiver league ignores faab_bid."""
        settings = parse_league_settings(load("league_settings.json"))
        assert settings.uses_faab is True

    def test_reads_league_shape(self) -> None:
        settings = parse_league_settings(load("league_settings.json"))
        assert settings.num_teams == 12
        assert settings.playoff_start_week == 15
        assert settings.league_key == "470.l.1000"

    def test_missing_roster_positions_is_schema_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="roster_positions"):
            parse_league_settings(load("malformed/settings_no_roster_positions.json"))

    def test_an_unknown_starting_slot_raises_instead_of_shrinking_the_lineup(self) -> None:
        """Dropping a slot we cannot read would quietly start one fewer player."""
        with pytest.raises(SchemaDrift, match="OP"):
            parse_league_settings(load("malformed/settings_unknown_slot.json"))


class TestRoster:
    def test_reads_every_player_with_their_slot(self) -> None:
        roster = parse_roster(load("roster_week2.json"), week=2)
        assert roster.team_key == "470.l.1000.t.6"
        assert roster.week == 2
        assert len(roster.spots) == 12

    def test_separates_starters_from_bench_and_ir(self) -> None:
        roster = parse_roster(load("roster_week2.json"), week=2)
        assert len(roster.starters) == 9
        benched = {s.player.name for s in roster.spots if s.slot is Slot.BENCH}
        assert benched == {"Tyrone Tracy", "Jordan Addison"}
        assert [s.player.name for s in roster.spots if s.slot is Slot.IR] == ["Brock Bowers"]

    def test_reads_flex_eligibility(self) -> None:
        roster = parse_roster(load("roster_week2.json"), week=2)
        bijan = next(s.player for s in roster.spots if s.player.name == "Bijan Robinson")
        assert bijan.position is Position.RB
        assert Slot.FLEX in bijan.eligible_slots
        assert bijan.can_fill(Slot.FLEX) is True
        assert bijan.can_fill(Slot.WR) is False

    def test_reads_injury_status(self) -> None:
        roster = parse_roster(load("roster_week2.json"), week=2)
        odunze = next(s.player for s in roster.spots if s.player.name == "Rome Odunze")
        assert odunze.injury_status == "Q"
        bijan = next(s.player for s in roster.spots if s.player.name == "Bijan Robinson")
        assert bijan.injury_status is None

    def test_defense_parses_as_a_position(self) -> None:
        roster = parse_roster(load("roster_week2.json"), week=2)
        dst = next(s for s in roster.spots if s.player.position is Position.DEF)
        assert dst.slot is Slot.DEF

    def test_an_empty_roster_is_schema_drift_not_an_empty_list(self) -> None:
        with pytest.raises(SchemaDrift, match="empty"):
            parse_roster(load("malformed/roster_empty.json"), week=2)


class TestPlayers:
    def test_reads_free_agents_with_ownership(self) -> None:
        players = parse_players(load("free_agents.json"))
        assert [p.name for p in players] == ["Jaylen Wright", "Luke McCaffrey", "Cade Otton"]
        assert players[0].percent_rostered == 14.0
        assert players[2].percent_rostered == 31.0

    def test_ownership_sub_resource_does_not_corrupt_the_player(self) -> None:
        """The interleaved sub-resource carries its own 'week' key. It must not win."""
        players = parse_players(load("free_agents.json"))
        assert players[1].team == "WAS"
        assert players[1].injury_status == "Q"

    def test_an_unreadable_position_raises_rather_than_defaulting(self) -> None:
        with pytest.raises(SchemaDrift, match="position"):
            parse_players(load("malformed/player_no_position.json"))

    def test_a_response_with_no_players_collection_is_schema_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="players"):
            parse_players({"fantasy_content": {"league": [{"league_key": "470.l.1000"}]}})


class TestMatchup:
    def test_picks_the_opponent_by_key_never_by_position(self) -> None:
        matchup = parse_matchup(load("matchup_week2.json"), "470.l.1000.t.6", week=2)
        assert matchup.my_team == "470.l.1000.t.6"
        assert matchup.opponent == "470.l.1000.t.3"

    def test_works_from_the_other_side_of_the_same_matchup(self) -> None:
        matchup = parse_matchup(load("matchup_week2.json"), "470.l.1000.t.3", week=2)
        assert matchup.opponent == "470.l.1000.t.6"

    def test_matchups_alone_carry_no_opponent_starters(self) -> None:
        """Yahoo's matchups response has the score, not the roster. The client reads the
        opponent's roster separately; this records that the payload really lacks them."""
        matchup = parse_matchup(load("matchup_week2.json"), "470.l.1000.t.6", week=2)
        assert matchup.opponent_starters == ()

    def test_a_week_with_no_matchup_for_this_team_raises(self) -> None:
        with pytest.raises(SchemaDrift, match="no week 2 matchup"):
            parse_matchup(load("matchup_week2.json"), "470.l.1000.t.99", week=2)
