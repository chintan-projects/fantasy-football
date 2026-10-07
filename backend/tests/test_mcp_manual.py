"""The MCP server in manual league mode, end to end: screenshots in, decisions out.

This is the path the owner actually uses until Yahoo approves the API: send screenshots
to Claude on a phone, Claude calls the ff_enter_ tools, then asks for a lineup or a bid.
The decision tools are the same ones Yahoo mode uses, so what is under test is the join --
that what is entered by hand is enough for every decision, and that every decision says
where its inputs came from.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fakes import fixture_sources

from ff.adapters.manual import ManualLeague
from ff.adapters.sleeper import parse_players
from ff.adapters.store import Store
from ff.api.deps import Deps
from ff.api.mcp import build_server
from ff.core.config import Settings
from ff.domain.models import Player

PLAYERS = Path(__file__).parent / "fixtures" / "sleeper" / "players_sample.json"
WEEK = 5

MANUAL_TOOLS = {
    "ff_enter_roster",
    "ff_enter_free_agents",
    "ff_update_league",
    "ff_enter_transactions",
    "ff_league_inputs",
}


class ManualSleeper:
    """The week oracle and the player directory, from a recording."""

    def current_week(self) -> int:
        return WEEK

    def players(self) -> list[Player]:
        return parse_players(json.loads(PLAYERS.read_text()))


def build(tmp_path: Path) -> Deps:
    config = Settings(
        league_source="manual",
        yahoo_league_key="1340676",
        database_path=str(tmp_path / "ff.db"),
        monte_carlo_draws=400,
        random_seed=7,
    )
    store = Store(config.database_path)
    return Deps(
        yahoo=ManualLeague(store, league_key=config.yahoo_league_key),
        sources=list(fixture_sources()),
        store=store,
        sleeper=ManualSleeper(),  # type: ignore[arg-type]
        config=config,
    )


def call(deps: Deps, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = asyncio.run(build_server(deps).call_tool(name, args or {}))
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def call_error(deps: Deps, name: str, args: dict[str, Any] | None = None) -> str:
    """The message Claude sees when a tool refuses."""
    try:
        asyncio.run(build_server(deps).call_tool(name, args or {}))
    except Exception as exc:  # FastMCP wraps the tool's exception; the text is what matters
        return str(exc)
    raise AssertionError(f"{name} did not refuse")


def p(name: str, position: str, team: str | None = None, slot: str | None = None) -> dict[str, Any]:
    return {"name": name, "position": position, "team": team, "slot": slot}


MY_TEAM_SCREEN = [
    p("J. Allen", "QB", "BUF", "QB"),
    p("Bijan Robinson", "RB", "ATL", "RB"),
    p("C. McCaffrey", "RB", "SF", "RB"),
    p("J. Chase", "WR", "CIN", "WR"),
    p("P. Nacua", "WR", "LAR", "WR"),
    p("A. St. Brown", "WR", "DET", "WR"),
    p("T. Kelce", "TE", "KC", "TE"),
    p("R. Shaheed", "WR", None, "W/R/T"),
    p("B. Aubrey", "K", "DAL", "K"),
    p("Denver", "DEF", "DEN", "DEF"),
    p("T. Shough", "QB", "NO", "BN"),
    p("K. Walker", "RB", None, "BN"),
    p("T. Bigsby", "RB", None, "BN"),
    p("J. Reed", "WR", "GB", "BN"),
    p("S. LaPorta", "TE", "DET", "BN"),
    p("J. Elliott", "K", "PHI", "BN"),
]

MATCHUP_SCREEN = [
    p("L. Jackson", "QB", "BAL"),
    p("D. Henry", "RB", "BAL"),
    p("J. Gibbs", "RB", "DET"),
    p("J. Jefferson", "WR", "MIN"),
    p("C. Lamb", "WR", "DAL"),
    p("D. London", "WR", "ATL"),
    p("G. Kittle", "TE", "SF"),
    p("B. Hall", "RB", None, "W/R/T"),
    p("J. Elliott", "K", "PHI"),
    p("Bills", "DEF", "BUF"),
]

PLAYERS_SCREEN = [
    {"name": "Luke McCaffrey", "position": "WR", "team": "WAS", "percent_rostered": 22},
    {"name": "Jaylin Lane", "position": "WR", "team": "WAS", "percent_rostered": 9},
    {"name": "Garrett Wilson", "position": "WR", "team": "NYJ", "percent_rostered": 61},
    {"name": "Javonte Williams", "position": "RB", "team": "DAL", "percent_rostered": 48},
    {"name": "Jaylen Wright", "position": "RB", "team": "MIA", "percent_rostered": 12},
    {"name": "Sam LaPorta", "position": "TE", "team": "DET", "percent_rostered": 70},
]


def enter_everything(deps: Deps) -> None:
    assert call(deps, "ff_enter_roster", {"players": MY_TEAM_SCREEN})["saved"]
    opponent = {"players": MATCHUP_SCREEN, "whose": "opponent", "opponent_name": "Gridiron Gang"}
    assert call(deps, "ff_enter_roster", opponent)["saved"]
    assert call(deps, "ff_enter_free_agents", {"players": PLAYERS_SCREEN})["saved"]
    assert call(deps, "ff_update_league", {"faab_remaining": 86})["saved"]


# ---- the surface -----------------------------------------------------------------


def test_manual_tools_exist_only_in_manual_mode(tmp_path: Path) -> None:
    names = {t.name for t in asyncio.run(build_server(build(tmp_path)).list_tools())}
    assert names >= MANUAL_TOOLS
    assert "ff_recommend_lineup" in names, "the decision tools are the same in both modes"


def test_the_entry_tools_change_nothing_in_yahoo_and_say_so(tmp_path: Path) -> None:
    tools = {t.name: t for t in asyncio.run(build_server(build(tmp_path)).list_tools())}
    for name in MANUAL_TOOLS - {"ff_league_inputs"}:
        tool = tools[name]
        assert tool.annotations is not None and tool.annotations.destructive_hint is False, name
        assert "changes nothing in yahoo" in (tool.description or "").lower(), name
    assert tools["ff_league_inputs"].annotations.read_only_hint is True  # type: ignore[union-attr]


def test_the_server_tells_claude_not_to_invent_names(tmp_path: Path) -> None:
    instructions = build_server(build(tmp_path)).instructions or ""
    assert "manual mode" in instructions
    assert "Never fill in a name you cannot read" in instructions


# ---- screenshots in --------------------------------------------------------------


def test_a_roster_with_an_unreadable_name_is_sent_back(tmp_path: Path) -> None:
    deps = build(tmp_path)
    screen = [*MY_TEAM_SCREEN[:-1], p("B. Robinson", "RB", "ATL", "BN")]
    result = call(deps, "ff_enter_roster", {"players": screen})
    assert result["saved"] is False
    assert "Bijan Robinson" in result["problems"][0]
    assert "Nothing was saved" in result["next_step"]


def test_a_corrected_spelling_is_reported_back(tmp_path: Path) -> None:
    screen = [p("Puka Nakua", "WR", "LAR")]
    result = call(build(tmp_path), "ff_enter_free_agents", {"players": screen})
    assert result["corrected"] == ["Read 'Puka Nakua' as Puka Nacua."]


def test_what_is_still_needed_before_anything_is_entered(tmp_path: Path) -> None:
    status = call(build(tmp_path), "ff_league_inputs")
    assert status["week"] == WEEK
    assert len(status["still_needed"]) == 5  # roster, balance, free agents, opponent, settings
    assert not any(i["usable"] for i in status["inputs"] if i["input"] == "your roster")


# ---- decisions out ---------------------------------------------------------------


def test_a_lineup_before_the_roster_asks_for_the_screenshot(tmp_path: Path) -> None:
    message = call_error(build(tmp_path), "ff_recommend_lineup")
    assert "screenshot of your team page" in message


def test_a_lineup_without_an_opponent_asks_for_the_matchup_page(tmp_path: Path) -> None:
    deps = build(tmp_path)
    call(deps, "ff_enter_roster", {"players": MY_TEAM_SCREEN})
    assert "matchup page" in call_error(deps, "ff_recommend_lineup")


def test_the_full_sunday_lineup_from_screenshots(tmp_path: Path) -> None:
    deps = build(tmp_path)
    enter_everything(deps)
    plan = call(deps, "ff_recommend_lineup")
    assert plan["week"] == WEEK
    assert 0.0 < plan["win_probability"] < 1.0, "an opponent was entered, so this is a contest"
    assert len(plan["starters"]) == 10
    caveats = " ".join(plan["caveats"])
    assert "Roster as you sent it" in caveats
    assert "assumed" in caveats, "settings were never confirmed, and the answer must say so"
    assert deps.store.latest_snapshot(WEEK, "lineup") is not None, "still scorable later"


class Unprojecting:
    """A forecaster with nothing to say about one player, as on a bye or after an injury."""

    def __init__(self, inner: Any, missing: str) -> None:
        self.inner, self.missing = inner, missing
        self.name, self.required = inner.name, inner.required

    def weekly(self, week: int, players: list[Player]) -> dict[Any, float]:
        return {k: v for k, v in self.inner.weekly(week, players).items() if k != self.missing}

    def status(self) -> Any:
        return self.inner.status()


def test_an_unprojected_opponent_is_named_not_numbered(tmp_path: Path) -> None:
    """Found running this mode against live data on 2026-10-06: Lamar Jackson came back
    as "4881"."""
    deps = build(tmp_path)
    lamar = "4881"
    deps = Deps(
        yahoo=deps.yahoo,
        sources=[Unprojecting(s, lamar) for s in deps.sources],
        store=deps.store,
        sleeper=deps.sleeper,
        config=deps.config,
    )
    enter_everything(deps)
    assert call(deps, "ff_recommend_lineup")["unprojected"] == ["Lamar Jackson"]


def test_the_full_tuesday_bid_from_screenshots(tmp_path: Path) -> None:
    deps = build(tmp_path)
    enter_everything(deps)
    board = call(deps, "ff_waiver_board", {"limit": 5})
    assert board["targets"]
    rostered = {s["name"] for s in MY_TEAM_SCREEN}
    assert "Sam LaPorta" not in {t["player"] for t in board["targets"]}, "he is on my roster"
    assert not rostered & {t["player"] for t in board["targets"]}

    target = board["targets"][0]["player"]
    bid = call(deps, "ff_recommend_bid", {"player": target})
    assert bid["remaining_budget"] == 86
    assert 0 <= bid["recommended_bid"] <= 86
    assert any("not read from Yahoo" in c for c in bid["caveats"])


def test_the_claim_still_needs_approval_and_ends_in_a_yahoo_link(tmp_path: Path) -> None:
    """Manual mode changes where inputs come from. It does not change the two-step write."""
    deps = build(tmp_path)
    enter_everything(deps)
    target = call(deps, "ff_waiver_board", {"limit": 1})["targets"][0]["player"]
    proposal = call(deps, "ff_propose_claim", {"player": target})
    assert proposal["submitted"] is False and proposal["approval_id"]
    assert "Leaves $" in proposal["summary"]


def test_my_roster_lists_what_was_entered(tmp_path: Path) -> None:
    deps = build(tmp_path)
    enter_everything(deps)
    roster = call(deps, "ff_my_roster")
    assert {p["name"] for p in roster["players"]} >= {"Josh Allen", "Amon-Ra St. Brown"}


def test_health_reports_manual_mode_without_yahoo_credentials(tmp_path: Path) -> None:
    deps = build(tmp_path)
    assert deps.config.missing_required() == []
    assert deps.config.league_source == "manual"
