"""Sleeper's weekly projections.

The source that removes the paid dependency. FantasyPros wanted $8.99/mo to be the
required half of the ensemble; Sleeper serves Rotowire's numbers for nothing, covers every
position group whole including team defenses, and needs no key.

What is tested here is mostly refusal: an undocumented endpoint on an unversioned host
earns no trust, so the shape is checked on every fetch and every way of returning a
plausible-looking 200 with no usable projection in it has to raise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ff.adapters.sleeper import (
    SCORING_KEYS,
    SleeperProjections,
    index_projections,
    validate_projections,
)
from ff.core.cache import FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position

FIXTURES = Path(__file__).parent / "fixtures" / "sleeper"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def player(name: str, position: Position, team: str, pid: str = "p1") -> Player:
    return Player(
        id=PlayerId(pid),
        name=name,
        position=position,
        team=team,
        eligible_slots=frozenset(),
    )


def build(
    tmp_path: Path, payload: Any, *, status: int = 200, scoring: str = "ppr"
) -> SleeperProjections:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    return SleeperProjections(
        2026,
        FileCache(tmp_path / "cache"),
        scoring=scoring,  # type: ignore[arg-type]
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class TestValidation:
    """Checked on every fetch, not at startup. The endpoint is undocumented."""

    def test_the_recorded_payload_passes(self) -> None:
        validate_projections(load("projections_week2.json"), "pts_ppr")

    def test_an_empty_list_is_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="empty"):
            validate_projections(load("malformed/empty_list.json"), "pts_ppr")

    def test_a_non_list_is_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="not a list"):
            validate_projections(load("malformed/not_a_list.json"), "pts_ppr")

    def test_rows_without_any_projection_are_drift(self) -> None:
        """The dangerous 200: everything parses, nothing projects, and without this the
        app silently runs on one source."""
        with pytest.raises(SchemaDrift, match="not one carries"):
            validate_projections(load("malformed/no_scored_projection.json"), "pts_ppr")

    def test_a_row_without_a_player_is_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="no 'player'"):
            validate_projections(load("malformed/row_without_player.json"), "pts_ppr")

    def test_validation_is_scoring_aware(self) -> None:
        """A league on standard scoring is not served by checking the PPR key."""
        payload = load("projections_week2.json")
        for row in payload:
            row.get("stats", {}).pop("pts_std", None)
        with pytest.raises(SchemaDrift, match="pts_std"):
            validate_projections(payload, "pts_std")


class TestIndexing:
    def test_players_are_keyed_for_cross_source_matching(self) -> None:
        index = index_projections(load("projections_week2.json"), "pts_ppr")
        assert index
        assert all(":" in key for key in index)
        assert all(isinstance(v, float) for v in index.values())

    def test_defenses_are_keyed_by_team_not_by_nickname(self) -> None:
        """Sleeper says "Ravens", Yahoo says "Baltimore". Only the team abbreviation is
        common to both, which is why match_key joins defenses on it."""
        index = index_projections(load("projections_week2.json"), "pts_ppr")
        defense_keys = [k for k in index if k.startswith("DEF:")]
        assert defense_keys
        assert all(len(k.split(":")[1]) <= 3 for k in defense_keys)

    def test_rows_with_no_projection_are_skipped_not_zeroed(self) -> None:
        """A punter with no projection is not a player projected to score nothing."""
        payload = load("projections_week2.json")
        unscored = [r for r in payload if (r.get("stats") or {}).get("pts_ppr") is None]
        assert unscored, "the fixture is meant to carry unscored rows"
        index = index_projections(payload, "pts_ppr")
        assert len(index) == len(payload) - len(unscored)
        assert 0.0 not in index.values()

    def test_a_non_numeric_point_value_raises(self) -> None:
        with pytest.raises(SchemaDrift, match="not a number"):
            index_projections(load("malformed/points_not_a_number.json"), "pts_ppr")

    def test_scoring_format_changes_the_numbers(self) -> None:
        payload = load("projections_week2.json")
        ppr = index_projections(payload, "pts_ppr")
        std = index_projections(payload, "pts_std")
        shared = set(ppr) & set(std)
        assert shared
        # Receptions are worth a point in PPR and nothing in standard, so a pass catcher
        # must not score the same in both.
        assert any(ppr[k] != std[k] for k in shared)

    def test_every_scoring_format_is_reachable(self) -> None:
        payload = load("projections_week2.json")
        for key in SCORING_KEYS.values():
            assert index_projections(payload, key), f"{key} indexed nothing"


class TestProjectionSource:
    def test_it_matches_roster_players_to_projections(self, tmp_path: Path) -> None:
        payload = load("projections_week2.json")
        first = next(r for r in payload if (r.get("stats") or {}).get("pts_ppr") is not None)
        name = f"{first['player']['first_name']} {first['player']['last_name']}"
        position = Position(first["player"]["position"])

        source = build(tmp_path, payload)
        roster = [player(name, position, first["team"], pid="yahoo-1")]
        out = source.weekly(2, roster)
        assert out[PlayerId("yahoo-1")] == pytest.approx(first["stats"]["pts_ppr"])

    def test_an_uncovered_player_is_absent_not_zero(self, tmp_path: Path) -> None:
        source = build(tmp_path, load("projections_week2.json"))
        out = source.weekly(2, [player("Nobody Atall", Position.WR, "ATL", pid="x")])
        assert out == {}

    def test_a_non_200_is_a_source_failure(self, tmp_path: Path) -> None:
        source = build(tmp_path, "upstream is down", status=503)
        with pytest.raises(SourceUnavailable, match="503"):
            source.weekly(2, [])

    def test_a_200_that_is_not_json_is_drift(self, tmp_path: Path) -> None:
        source = build(tmp_path, "<html>nope</html>", status=200)
        with pytest.raises(SchemaDrift, match="not JSON"):
            source.weekly(2, [])

    def test_it_is_required(self, tmp_path: Path) -> None:
        """Unlike ESPN. ESPN's pull is capped and sorted by percent owned, so it drops
        deep-bench players; Sleeper returns each position group whole. If Sleeper is down
        there is no ensemble left to fall back to."""
        source = build(tmp_path, load("projections_week2.json"))
        assert source.required is True
        assert source.name == "sleeper"

    def test_a_failure_shows_up_in_status(self, tmp_path: Path) -> None:
        source = build(tmp_path, "gone", status=503)
        with pytest.raises(SourceUnavailable):
            source.weekly(2, [])
        status = source.status()
        assert status.ok is False
        assert status.required is True
        assert status.detail is not None

    def test_a_success_clears_the_status(self, tmp_path: Path) -> None:
        source = build(tmp_path, load("projections_week2.json"))
        source.weekly(2, [])
        assert source.status().ok is True

    def test_the_response_is_cached_per_week(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            assert "position[]=DEF" in str(request.url)
            assert "season_type=regular" in str(request.url)
            return httpx.Response(200, json=load("projections_week2.json"))

        source = SleeperProjections(
            2026,
            FileCache(tmp_path / "cache"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        source.weekly(2, [])
        source.weekly(2, [])
        assert calls["n"] == 1
        source.weekly(3, [])
        assert calls["n"] == 2
