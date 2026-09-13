"""Blending sources into projections.

The rule under test throughout: a projection is an ensemble or it is nothing. Every way of
ending up with one source has to fail, and they fail differently depending on where the
problem is -- config, fetch, or one player's coverage.
"""

from __future__ import annotations

import pytest

from ff.core.errors import ConfigError, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position, SourceStatus
from ff.services.projections import build_projections


class FakeSource:
    """An in-memory ProjectionSource. services/ tests use fakes, not mocks."""

    def __init__(
        self,
        name: str,
        values: dict[str, float],
        *,
        required: bool = False,
        fails: bool = False,
    ) -> None:
        self.name = name
        self.required = required
        self._values = values
        self._fails = fails

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        if self._fails:
            raise SourceUnavailable(self.name, "HTTP 503", required=self.required)
        return {p.id: self._values[str(p.id)] for p in players if str(p.id) in self._values}

    def status(self) -> SourceStatus:
        return SourceStatus(name=self.name, ok=not self._fails, required=self.required)


def player(pid: str, position: Position = Position.RB) -> Player:
    return Player(
        id=PlayerId(pid),
        name=f"Player {pid}",
        position=position,
        team="ATL",
        eligible_slots=frozenset(),
    )


ROSTER = [player("a"), player("b"), player("c")]


class TestMinimumSources:
    def test_one_configured_source_fails_before_any_fetch(self) -> None:
        with pytest.raises(ConfigError, match="63%"):
            build_projections([FakeSource("espn", {"a": 10.0})], 1, ROSTER)

    def test_zero_configured_sources_fail(self) -> None:
        with pytest.raises(ConfigError, match="none"):
            build_projections([], 1, ROSTER)

    def test_one_answering_source_fails_after_the_fetch(self) -> None:
        """Two configured, one degraded -- still not an ensemble."""
        sources = [
            FakeSource("espn", {"a": 10.0}, fails=True),
            FakeSource("fantasypros", {"a": 12.0}),
        ]
        with pytest.raises(SourceUnavailable, match="misconfiguration"):
            build_projections(sources, 1, ROSTER)

    def test_an_empty_response_counts_as_no_answer(self) -> None:
        sources = [FakeSource("espn", {}), FakeSource("fantasypros", {"a": 12.0})]
        with pytest.raises(SourceUnavailable, match="only 1 source"):
            build_projections(sources, 1, ROSTER)


class TestFailureRouting:
    def test_a_required_source_stops_the_run(self) -> None:
        sources = [
            FakeSource("fantasypros", {"a": 12.0}, required=True, fails=True),
            FakeSource("espn", {"a": 10.0}),
            FakeSource("other", {"a": 11.0}),
        ]
        with pytest.raises(SourceUnavailable) as caught:
            build_projections(sources, 1, ROSTER)
        assert caught.value.source == "fantasypros"

    def test_an_optional_source_degrades_into_a_note(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0}, fails=True),
            FakeSource("fantasypros", {"a": 12.0, "b": 8.0}),
            FakeSource("other", {"a": 11.0, "b": 9.0}),
        ]
        result = build_projections(sources, 1, ROSTER)
        assert any("espn was unavailable" in note for note in result.notes)
        assert PlayerId("a") in result.projections

    def test_every_source_reports_its_status(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0}, fails=True),
            FakeSource("fantasypros", {"a": 12.0}),
            FakeSource("other", {"a": 11.0}),
        ]
        result = build_projections(sources, 1, ROSTER)
        assert {s.name for s in result.sources} == {"espn", "fantasypros", "other"}
        assert [s.ok for s in result.sources if s.name == "espn"] == [False]


class TestPerPlayerCoverage:
    def test_a_player_with_one_source_gets_no_projection(self) -> None:
        """One source's number is not a thinner ensemble. It is a different thing."""
        sources = [
            FakeSource("espn", {"a": 10.0, "b": 14.0}),
            FakeSource("fantasypros", {"a": 12.0}),
        ]
        result = build_projections(sources, 1, ROSTER)
        assert set(result.projections) == {PlayerId("a")}
        assert set(result.unprojected) == {PlayerId("b"), PlayerId("c")}

    def test_the_gap_is_said_out_loud(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0, "b": 14.0}),
            FakeSource("fantasypros", {"a": 12.0}),
        ]
        result = build_projections(sources, 1, ROSTER)
        assert any("fewer than 2 sources" in note for note in result.notes)

    def test_coverage_is_reported(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0, "b": 14.0}),
            FakeSource("fantasypros", {"a": 12.0, "b": 16.0}),
        ]
        result = build_projections(sources, 1, ROSTER)
        assert result.coverage == pytest.approx(2 / 3)

    def test_full_coverage_produces_no_note(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0, "b": 14.0, "c": 6.0}),
            FakeSource("fantasypros", {"a": 12.0, "b": 16.0, "c": 8.0}),
        ]
        result = build_projections(sources, 1, ROSTER)
        assert result.notes == ()
        assert result.coverage == 1.0


class TestBlending:
    def test_the_mean_is_the_average_of_the_sources(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0}),
            FakeSource("fantasypros", {"a": 14.0}),
        ]
        result = build_projections(sources, 1, [player("a")])
        assert result.projections[PlayerId("a")].mean == 12.0

    def test_source_disagreement_becomes_epistemic_spread(self) -> None:
        agree = build_projections(
            [FakeSource("espn", {"a": 12.0}), FakeSource("fantasypros", {"a": 12.0})],
            1,
            [player("a")],
        )
        disagree = build_projections(
            [FakeSource("espn", {"a": 6.0}), FakeSource("fantasypros", {"a": 18.0})],
            1,
            [player("a")],
        )
        a = PlayerId("a")
        assert disagree.projections[a].epistemic_sd > agree.projections[a].epistemic_sd
        assert disagree.projections[a].mean == agree.projections[a].mean

    def test_aleatoric_spread_comes_from_the_position_prior(self) -> None:
        """Two positions, same projected points, different week-to-week volatility."""
        rb = build_projections(
            [FakeSource("espn", {"a": 12.0}), FakeSource("fantasypros", {"a": 12.0})],
            1,
            [player("a", Position.RB)],
        )
        dst = build_projections(
            [FakeSource("espn", {"a": 12.0}), FakeSource("fantasypros", {"a": 12.0})],
            1,
            [player("a", Position.DEF)],
        )
        a = PlayerId("a")
        assert dst.projections[a].aleatoric_sd > rb.projections[a].aleatoric_sd

    def test_the_projection_records_which_sources_made_it(self) -> None:
        sources = [
            FakeSource("espn", {"a": 10.0}),
            FakeSource("fantasypros", {"a": 14.0}),
        ]
        result = build_projections(sources, 1, [player("a")])
        assert result.projections[PlayerId("a")].sources == ("espn", "fantasypros")
