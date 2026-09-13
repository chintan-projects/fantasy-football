"""nflverse injuries.

The fixture is real: rows from ``injuries_2026.parquet`` via nflreadpy, recorded
2026-09-12. Two of its assertions exist because the published column description turned out
to be wrong -- see the module docstring on adapters/nflverse.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ff.adapters.nflverse import (
    NflverseInjuries,
    index_by_match_key,
    normalize_practice_status,
    to_rows,
    validate,
)
from ff.core.cache import FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position, Slot

FIXTURES = Path(__file__).parent / "fixtures" / "nflverse"


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


def source(tmp_path: Path, rows: Any) -> NflverseInjuries:
    return NflverseInjuries(2026, FileCache(tmp_path / "cache"), loader=lambda _: rows)


class TestPracticeStatus:
    """The published description says Full/Limited/DNP. The data says otherwise."""

    def test_the_real_phrases_normalize(self) -> None:
        assert normalize_practice_status("Full Participation in Practice") == "FULL"
        assert normalize_practice_status("Limited Participation in Practice") == "LIMITED"
        assert normalize_practice_status("Did Not Participate In Practice") == "DNP"

    def test_the_documented_short_forms_also_normalize(self) -> None:
        """Accepted in case nflverse ever ships what its docs describe."""
        assert normalize_practice_status("Full") == "FULL"
        assert normalize_practice_status("DNP") == "DNP"

    def test_null_and_unknown_stay_none(self) -> None:
        assert normalize_practice_status(None) is None
        assert normalize_practice_status("") is None
        assert normalize_practice_status("Rested") is None


class TestValidation:
    def test_the_recorded_columns_pass(self) -> None:
        validate(load("injuries_2026.json"))

    def test_a_missing_column_names_it(self) -> None:
        with pytest.raises(SchemaDrift, match="practice_status"):
            validate(load("malformed/missing_practice_status_column.json"))

    def test_a_renamed_column_names_it(self) -> None:
        with pytest.raises(SchemaDrift, match="full_name"):
            validate(load("malformed/renamed_full_name_column.json"))

    def test_an_empty_frame_is_valid(self) -> None:
        """There is no injury report in the offseason. That is not drift."""
        validate([])

    def test_a_frame_we_cannot_read_raises(self) -> None:
        with pytest.raises(SchemaDrift, match="cannot read"):
            to_rows(object())


class TestIndexing:
    def test_reads_a_real_report_line(self) -> None:
        index = index_by_match_key(load("injuries_2026.json"), 2026, 1)
        odunze = index["WR:rome odunze"]
        assert odunze.report_status == "Questionable"
        assert odunze.practice_status == "LIMITED"
        assert odunze.primary_injury == "Calf"

    def test_out_is_the_one_designation_that_is_not_a_judgement_call(self) -> None:
        index = index_by_match_key(load("injuries_2026.json"), 2026, 1)
        assert index["TE:brock bowers"].is_out is True
        assert index["WR:rome odunze"].is_out is False

    def test_a_full_practice_can_still_carry_a_game_designation(self) -> None:
        """Nabers practised fully and is still Questionable. Both fields matter."""
        index = index_by_match_key(load("injuries_2026.json"), 2026, 1)
        nabers = index["WR:malik nabers"]
        assert nabers.practice_status == "FULL"
        assert nabers.report_status == "Questionable"

    def test_offensive_line_and_defenders_are_skipped(self) -> None:
        """They fill most of a real report and this app models none of them."""
        index = index_by_match_key(load("injuries_2026.json"), 2026, 1)
        assert all(key.split(":")[0] in {"QB", "RB", "WR", "TE", "K", "DEF"} for key in index)

    def test_another_week_is_not_this_week(self) -> None:
        assert index_by_match_key(load("injuries_2026.json"), 2026, 2) == {}

    def test_another_season_is_not_this_season(self) -> None:
        assert index_by_match_key(load("injuries_2026.json"), 2025, 1) == {}


class TestSource:
    def test_matches_to_yahoo_player_ids(self, tmp_path: Path) -> None:
        nflverse = source(tmp_path, load("injuries_2026.json"))
        out = nflverse.weekly(1, [player("470.p.33012", "Rome Odunze", Position.WR, "CHI")])
        assert out[PlayerId("470.p.33012")].report_status == "Questionable"

    def test_an_unreported_player_is_absent_not_marked_healthy(self, tmp_path: Path) -> None:
        """Coverage is partial -- 182 rows in week 1 against a typical 400-600. No row
        means nothing is known, which is not the same as fit."""
        nflverse = source(tmp_path, load("injuries_2026.json"))
        out = nflverse.weekly(1, [player("470.p.30977", "Bijan Robinson", Position.RB, "ATL")])
        assert out == {}

    def test_injuries_are_never_a_required_source(self, tmp_path: Path) -> None:
        assert source(tmp_path, []).required is False

    def test_a_loader_failure_degrades(self, tmp_path: Path) -> None:
        def boom(_: Any) -> Any:
            raise OSError("github is down")

        nflverse = NflverseInjuries(2026, FileCache(tmp_path / "c"), loader=boom)
        with pytest.raises(SourceUnavailable) as caught:
            nflverse.weekly(1, [])
        assert caught.value.required is False
        assert nflverse.status().ok is False

    def test_a_second_call_is_served_from_cache(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def counting(_: Any) -> Any:
            calls["n"] += 1
            return load("injuries_2026.json")

        nflverse = NflverseInjuries(2026, FileCache(tmp_path / "c"), loader=counting)
        nflverse.weekly(1, [])
        nflverse.weekly(1, [])
        assert calls["n"] == 1

    def test_status_reports_age_after_a_good_fetch(self, tmp_path: Path) -> None:
        nflverse = source(tmp_path, load("injuries_2026.json"))
        nflverse.weekly(1, [])
        status = nflverse.status()
        assert status.ok is True
        assert status.age_seconds is not None


class TestPolars:
    def test_reads_a_polars_frame(self) -> None:
        """nflreadpy returns Polars, not pandas. This is the real return type."""
        polars = pytest.importorskip("polars")
        frame = polars.DataFrame(load("injuries_2026.json"))
        rows = to_rows(frame)
        assert len(rows) == len(load("injuries_2026.json"))
        assert "practice_status" in rows[0]
