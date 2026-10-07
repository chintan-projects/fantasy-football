"""Firecrawl's board as six projection sources.

The WR and DST fixtures are real rows from the live page, 2026-10-06, week 5 -- see
tests/fixtures/firecrawl/README.md. The other positions are built here from a helper,
because the board fetch walks every position and only the WR and DST pages carry
assertions about values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ff.adapters.firecrawl import (
    SITES,
    FirecrawlBoard,
    FirecrawlSite,
    board_rows,
    check_formats,
    firecrawl_sources,
    flight_text,
    page_week,
    parse_page,
    scored,
)
from ff.core.cache import FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.domain.models import Player, PlayerId, Position, slots_for
from ff.services.projections import build_projections

FIXTURES = Path(__file__).parent / "fixtures" / "firecrawl"


def html(name: str) -> str:
    return (FIXTURES / name).read_text()


def page(week: int, rows: list[dict[str, Any]]) -> str:
    """A page in the real page's format: the title names the week, rows sit in a push."""
    body = ",".join(json.dumps(r, separators=(",", ":")) for r in rows)
    return (
        f"<html><head><title>Who to Start: Week {week} Start/Sit | Firecrawl</title></head>"
        f"<body><script>self.__next_f.push([1,{json.dumps(body)}])</script></body></html>"
    )


def rows_for(position: str, count: int = 12, catches: float = 0.0) -> list[dict[str, Any]]:
    """``catches`` is subtracted from every number, which is what standard scoring does."""
    return [
        {
            "key": f"player-{i}:{position}",
            "name": f"Player {position} {i}",
            "team": "DAL",
            "points": {"draftsharks-com": 10.0 + i - catches, "fantasydata-com": 9.0 + i - catches},
        }
        for i in range(count)
    ]


def player(pid: str, name: str, position: Position, team: str) -> Player:
    return Player(
        id=PlayerId(pid),
        name=name,
        position=position,
        team=team,
        eligible_slots=slots_for(position),
    )


JSN = player("1", "Jaxon Smith-Njigba", Position.WR, "SEA")
VIKINGS = player("2", "Minnesota Vikings", Position.DEF, "MIN")


def transport(week: int = 5, calls: list[str] | None = None) -> httpx.MockTransport:
    """The live page's routing: position and scoring in the query string."""

    def handle(request: httpx.Request) -> httpx.Response:
        position = request.url.params.get("position", "QB")
        scoring = request.url.params.get("scoring", "ppr")
        if calls is not None:
            calls.append(f"{position}:{scoring}")
        if position == "WR":
            name = "wr_std_week5.html" if scoring == "std" else "wr_ppr_week5.html"
            return httpx.Response(200, text=html(name).replace("Week 5", f"Week {week}"))
        if position == "DST":
            return httpx.Response(
                200, text=html("dst_week5.html").replace("Week 5", f"Week {week}")
            )
        catches = 2.0 if scoring == "std" else 0.0
        return httpx.Response(200, text=page(week, rows_for(position, catches=catches)))

    return httpx.MockTransport(handle)


def board(tmp_path: Path, scoring: Any = "half_ppr", **kw: Any) -> FirecrawlBoard:
    return FirecrawlBoard(
        FileCache(tmp_path / "cache"),
        scoring=scoring,
        client=httpx.Client(transport=transport(**kw)),
    )


class TestParsing:
    def test_reads_the_week_from_the_title(self) -> None:
        assert page_week(html("wr_ppr_week5.html")) == 5

    def test_a_page_without_a_week_is_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="no week"):
            page_week("<html><title>Rankings</title></html>")

    def test_only_rows_that_carry_points_are_board_rows(self) -> None:
        """The real page also lists players without points (a name index). Skipped."""
        rows = board_rows(flight_text(html("wr_ppr_week5.html")))
        assert all(isinstance(r["points"], dict) for r in rows)
        assert "Josh Allen" not in {r["name"] for r in rows}

    def test_every_site_on_a_real_row_is_read(self) -> None:
        jsn = parse_page(html("wr_ppr_week5.html"), Position.WR)["rows"][0]
        assert jsn["name"] == "Jaxon Smith-Njigba"
        assert jsn["points"] == {
            "draftsharks": 21.8,
            "cbssports": 22.4,
            "fftoday": 24.2,
            "ffcalculator": 23.2,
            "startwho": 20.4,
            "fantasydata": 23.2,
        }

    def test_an_ignored_position_parameter_is_caught(self) -> None:
        """Asked for ?position=DEF, the live page served quarterbacks. Recorded 2026-10-06."""
        with pytest.raises(SchemaDrift, match="position parameter was ignored"):
            parse_page(html("def_param_serves_qbs_week5.html"), Position.DEF)

    def test_too_few_rows_is_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="only 3"):
            parse_page(page(5, rows_for("TE", 3)), Position.TE)


class TestScoring:
    def test_half_ppr_is_the_mean_of_ppr_and_standard(self) -> None:
        """Draft Sharks has JSN at 21.8 PPR and 14.7 standard. The live ?scoring=half page
        said 18.3, which is this to one decimal."""
        ppr = parse_page(html("wr_ppr_week5.html"), Position.WR)["rows"]
        std = parse_page(html("wr_std_week5.html"), Position.WR)["rows"]
        half = scored(ppr, std, "half_ppr")
        assert half["draftsharks"]["WR:jaxon smith njigba"] == pytest.approx(18.25)
        assert scored(ppr, std, "std")["draftsharks"]["WR:jaxon smith njigba"] == 14.7
        assert scored(ppr, None, "ppr")["draftsharks"]["WR:jaxon smith njigba"] == 21.8

    def test_a_site_missing_one_format_gets_no_half_ppr_number(self) -> None:
        ppr = [{"name": "A", "team": "DAL", "position": "WR", "points": {"cbssports": 12.0}}]
        std = [{"name": "A", "team": "DAL", "position": "WR", "points": {}}]
        assert scored(ppr, std, "half_ppr") == {}

    def test_a_standard_page_that_is_really_ppr_is_refused(self) -> None:
        """?scoring=half_ppr is silently ignored by the live page and serves PPR."""
        ppr = parse_page(html("wr_ppr_week5.html"), Position.WR)["rows"]
        fake_std = parse_page(html("wr_halfppr_param_week5.html"), Position.WR)["rows"]
        with pytest.raises(SchemaDrift, match="scoring parameter was ignored"):
            check_formats(ppr, fake_std, Position.WR)

    def test_defenses_join_on_team(self) -> None:
        dst = parse_page(html("dst_week5.html"), Position.DEF)["rows"]
        assert scored(dst, None, "half_ppr")["draftsharks"]["DEF:MIN"] == 9.3


class TestBoard:
    def test_six_sources_one_per_site(self, tmp_path: Path) -> None:
        sources = firecrawl_sources(board(tmp_path))
        assert [s.name for s in sources] == list(SITES.values())
        assert all(s.required is False for s in sources)

    def test_each_site_projects_the_players_it_covers(self, tmp_path: Path) -> None:
        site = FirecrawlSite(board(tmp_path), "draftsharks")
        out = site.weekly(5, [JSN, VIKINGS])
        assert out[JSN.id] == pytest.approx(18.25)
        assert out[VIKINGS.id] == 9.3

    def test_one_fetch_per_page_serves_all_six(self, tmp_path: Path) -> None:
        calls: list[str] = []
        shared = board(tmp_path, calls=calls)
        for source in firecrawl_sources(shared):
            source.weekly(5, [JSN])
        # PPR for six positions, standard for the four that catch passes. Once each.
        assert len(calls) == 10
        assert len(set(calls)) == 10

    def test_ppr_leagues_skip_the_standard_pages(self, tmp_path: Path) -> None:
        calls: list[str] = []
        board(tmp_path, scoring="ppr", calls=calls).site_points(5)
        assert not any(c.endswith(":std") for c in calls)

    def test_a_page_for_another_week_is_refused_not_read(self, tmp_path: Path) -> None:
        """On Tuesday morning of week 5 the live page still showed week 4."""
        stale = board(tmp_path, week=4)
        with pytest.raises(SourceUnavailable) as caught:
            stale.site_points(5)
        assert caught.value.required is False
        assert "week 4, not week 5" in str(caught.value)
        assert stale.status().ok is False

    def test_an_http_error_degrades(self, tmp_path: Path) -> None:
        down = FirecrawlBoard(
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
        )
        with pytest.raises(SourceUnavailable, match="HTTP 503"):
            FirecrawlSite(down, "cbssports").weekly(5, [JSN])
        assert FirecrawlSite(down, "cbssports").status().ok is False


class Steady:
    """A required forecaster that always answers."""

    required = True

    def __init__(self, name: str) -> None:
        self.name = name

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        return {p.id: 15.0 for p in players}

    def status(self) -> Any:
        from ff.domain.models import SourceStatus

        return SourceStatus(name=self.name, ok=True, required=True, age_seconds=1.0)


def test_six_sites_down_together_make_one_note_not_six(tmp_path: Path) -> None:
    stale = board(tmp_path, week=4)
    sources = [Steady("espn"), Steady("sleeper"), *firecrawl_sources(stale)]
    blended = build_projections(sources, 5, [JSN])  # type: ignore[arg-type]
    assert len(blended.notes) == 1
    assert "draftsharks, cbssports" in blended.notes[0]
    assert JSN.id in blended.projections, "ESPN and Sleeper still carry the week"


def test_eight_forecasters_blend_into_one_projection(tmp_path: Path) -> None:
    sources = [Steady("espn"), Steady("sleeper"), *firecrawl_sources(board(tmp_path))]
    projection = build_projections(sources, 5, [JSN]).projections[JSN.id]  # type: ignore[arg-type]
    assert len(projection.sources) == 8
    assert projection.epistemic_sd > 1.0, "the sites disagree, and the spread says so"
