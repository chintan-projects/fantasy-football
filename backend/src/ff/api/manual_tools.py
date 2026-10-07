"""The tools that exist only in manual league mode: the owner's screenshots, entered.

Registered by ``build_server`` when ``FF_LEAGUE_SOURCE=manual``. In Yahoo mode they would
write to a store nothing reads, so they are not offered at all.

These record inputs; they decide nothing. The decision tools in ``mcp.py`` are unchanged
and read what these save, through ``adapters.manual.ManualLeague``. Kept out of ``mcp.py``
because that file is the decision surface and is already past the size limit.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ff.adapters.manual import Match, PlayerDirectory, PlayerEntry
from ff.api.deps import Deps
from ff.services import league_entry

MANUAL_INSTRUCTIONS = """

This league is in manual mode. Yahoo has not approved API access, so the roster, this
week's opponent, the available players and the FAAB balance come from the owner, usually
as screenshots of the Yahoo app. When one arrives, read every player off it and pass them
to the matching ff_enter_ tool exactly as shown -- abbreviated first names like "J. Allen"
are fine as long as position and team go with them. Never fill in a name you cannot read:
leave it out and say so. When a decision tool says an input is missing or out of date, ask
the owner for that screenshot. Do not work around it with players you remember."""

_WRITES_LOCAL_STATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_NOT_YAHOO = "Saves to this app's own database. Changes nothing in Yahoo and moves no money."


class ScreenPlayer(BaseModel):
    """One player, as read off a Yahoo screen."""

    name: str = Field(description="As shown, e.g. 'Josh Allen' or 'J. Allen'.")
    position: str | None = Field(
        default=None, description="QB, RB, WR, TE, K or DEF, as shown next to the name."
    )
    team: str | None = Field(default=None, description="NFL team as shown, e.g. BUF.")
    slot: str | None = Field(
        default=None,
        description="Roster slot as shown on the left: QB, RB, WR, TE, W/R/T, K, DEF, BN or IR.",
    )


class ScreenFreeAgent(BaseModel):
    name: str = Field(description="As shown, e.g. 'Luke McCaffrey'.")
    position: str | None = Field(default=None, description="QB, RB, WR, TE, K or DEF.")
    team: str | None = Field(default=None, description="NFL team as shown.")
    percent_rostered: float | None = Field(
        default=None, ge=0, le=100, description="Yahoo's '% Rostered' column, if visible."
    )


class ScreenBid(BaseModel):
    player: str = Field(description="The player added, as shown.")
    position: str | None = Field(default=None)
    team: str | None = Field(default=None, description="The player's NFL team.")
    amount: int = Field(ge=0, description="The winning FAAB bid in dollars.")
    manager: str = Field(description="The fantasy team that won the bid, as shown.")
    week: int = Field(ge=1, le=18, description="The NFL week the claim processed in.")
    mine: bool = Field(default=False, description="True if the owner made this claim.")


def register(mcp: FastMCP, d: Deps) -> None:
    """Add the manual-mode tools to a server."""

    def directory() -> PlayerDirectory:
        return PlayerDirectory(d.sleeper.players())

    def week_or_current(week: int | None) -> int:
        return week if week is not None else d.sleeper.current_week()

    @mcp.tool(
        name="ff_enter_roster",
        title="Enter a roster from a screenshot",
        annotations=_WRITES_LOCAL_STATE,
        description=(
            "Save the owner's roster, or this week's opponent's starters, as read off a "
            "screenshot of the Yahoo app.\n\n"
            "For the owner's roster send every player in one call: starters, bench and IR. "
            "Each call replaces the last, so a roster split across two screenshots goes in "
            "together. For the opponent use whose='opponent' with their starters from the "
            "matchup page.\n\n"
            "Nothing is saved unless every name matches exactly one player. When a name "
            "is ambiguous or unknown the answer says which one and why: read it again from "
            "the screen, add the position and team, and call again. Report any "
            "'corrected' names back to the owner so they can catch a misread.\n\n" + _NOT_YAHOO
        ),
    )
    def ff_enter_roster(
        players: Annotated[list[ScreenPlayer], Field(description="Every player on screen.")],
        whose: Annotated[
            Literal["mine", "opponent"], Field(description="The owner's team, or the opponent.")
        ] = "mine",
        opponent_name: Annotated[
            str | None, Field(description="The opponent's team name, if whose='opponent'.")
        ] = None,
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = week_or_current(week)
        result = league_entry.enter_roster(
            d.store,
            directory(),
            [_entry(p) for p in players],
            week=wk,
            whose=whose,
            opponent_name=opponent_name,
        )
        return {"week": wk, "whose": whose, **_shape(result)}

    @mcp.tool(
        name="ff_enter_free_agents",
        title="Enter available players from a screenshot",
        annotations=_WRITES_LOCAL_STATE,
        description=(
            "Save the list of available players, as read off the Players screen in the "
            "Yahoo app. This is the pool the waiver board and the bid tools choose from.\n\n"
            "Send everything visible across all the screenshots in one call; each call "
            "replaces the last list. Include '% Rostered' when it is shown. Names that "
            "cannot be matched are skipped and listed, not guessed.\n\n" + _NOT_YAHOO
        ),
    )
    def ff_enter_free_agents(
        players: Annotated[list[ScreenFreeAgent], Field(description="Every player on screen.")],
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = week_or_current(week)
        entries = [
            PlayerEntry(
                name=p.name, position=p.position, team=p.team, percent_rostered=p.percent_rostered
            )
            for p in players
        ]
        result = league_entry.enter_free_agents(d.store, directory(), entries, week=wk)
        return {"week": wk, **_shape(result)}

    @mcp.tool(
        name="ff_update_league",
        title="Set the FAAB balance or league settings",
        annotations=_WRITES_LOCAL_STATE,
        description=(
            "Record the owner's remaining FAAB, or the league's rules: roster slots, number "
            "of teams, total budget, first playoff week. Send only what changed.\n\n"
            "The balance goes stale after a week and the bid tools then ask for it again. "
            "Settings never go stale; until they are set, Yahoo's defaults are assumed and "
            "every decision says so.\n\n" + _NOT_YAHOO
        ),
    )
    def ff_update_league(
        faab_remaining: Annotated[
            int | None, Field(ge=0, le=1000, description="FAAB dollars left right now.")
        ] = None,
        faab_budget: Annotated[
            int | None, Field(ge=1, le=1000, description="The season's starting budget.")
        ] = None,
        num_teams: Annotated[int | None, Field(ge=2, le=20)] = None,
        playoff_start_week: Annotated[int | None, Field(ge=10, le=18)] = None,
        roster_slots: Annotated[
            dict[str, int] | None,
            Field(
                description="Every slot and its count, e.g. {'QB': 1, 'RB': 2, 'WR': 3, "
                "'TE': 1, 'W/R/T': 1, 'K': 1, 'DEF': 1, 'BN': 6, 'IR': 1}."
            ),
        ] = None,
    ) -> dict[str, Any]:
        problems = league_entry.update_league(
            d.store,
            week=d.sleeper.current_week(),
            faab_remaining=faab_remaining,
            faab_budget=faab_budget,
            num_teams=num_teams,
            playoff_start_week=playoff_start_week,
            roster_slots=roster_slots,
        )
        settings = d.yahoo.league_settings()
        return {
            "saved": not problems,
            "problems": problems,
            "num_teams": settings.num_teams,
            "faab_budget": settings.faab_budget,
            "playoff_start_week": settings.playoff_start_week,
            "roster_slots": {s.value: n for s, n in settings.slot_counts.items()},
        }

    @mcp.tool(
        name="ff_enter_transactions",
        title="Enter completed waiver claims",
        annotations=_WRITES_LOCAL_STATE,
        description=(
            "Add completed FAAB claims from the league's Transactions page: who won which "
            "player for how much, in which week. Sending the same page twice is harmless.\n\n"
            "This is what the bid tools learn the league's competition from. Without it they "
            "assume a quarter of the league wants every player, which is a guess. [folk] "
            "Yahoo shows winning bids only, so this measures what cleared, never what was "
            "offered. [empirical]\n\n" + _NOT_YAHOO
        ),
    )
    def ff_enter_transactions(
        bids: Annotated[list[ScreenBid], Field(description="Every completed claim on screen.")],
    ) -> dict[str, Any]:
        entries = [
            league_entry.Bid(
                player=PlayerEntry(name=b.player, position=b.position, team=b.team),
                amount=b.amount,
                manager=b.manager,
                week=b.week,
                mine=b.mine,
            )
            for b in bids
        ]
        return _shape(league_entry.enter_transactions(d.store, directory(), entries))

    @mcp.tool(
        name="ff_league_inputs",
        title="What I still need from you",
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        ),
        description=(
            "List every input the owner supplies by hand -- roster, FAAB balance, available "
            "players, this week's opponent, league settings, past claims -- with how old "
            "each one is and exactly what to send for anything missing or out of date.\n\n"
            "Call this at the start of a Sunday or Tuesday session, or whenever the owner "
            "asks what you need."
        ),
    )
    def ff_league_inputs(
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = week_or_current(week)
        states = league_entry.input_states(d.store, wk)
        return {
            "week": wk,
            "inputs": [
                {
                    "input": s.name,
                    "entered": s.entered,
                    "days_old": s.days_old,
                    "usable": s.fresh,
                    "detail": s.detail,
                }
                for s in states
            ],
            "still_needed": [s.ask for s in states if s.ask],
        }


def caveats(d: Deps) -> list[str]:
    """Where a decision's inputs came from, in manual mode. Empty in Yahoo mode."""
    if d.config.league_source != "manual":
        return []
    return league_entry.hand_entered_caveats(d.store)


def _entry(p: ScreenPlayer) -> PlayerEntry:
    return PlayerEntry(name=p.name, position=p.position, team=p.team, slot=p.slot)


def _shape(result: league_entry.EntryResult) -> dict[str, Any]:
    return {
        "saved": result.saved,
        "players": [_matched(m) for m in result.matched if m.player is not None],
        "problems": result.problems,
        "corrected": [
            f"Read '{m.entry.name}' as {m.player.name}."
            for m in result.matched
            if m.player is not None and m.how == "close"
        ],
        "warnings": result.warnings,
        "next_step": (
            "Saved."
            if result.saved and not result.problems
            else "Saved what matched; the problems list says what was skipped."
            if result.saved
            else "Nothing was saved. Fix the problems listed and send the whole list again."
        ),
    }


def _matched(m: Match) -> dict[str, Any]:
    assert m.player is not None
    return {
        "as_entered": m.entry.name,
        "player": m.player.name,
        "position": m.player.position.value,
        "nfl_team": m.player.team,
        "slot": m.entry.slot,
        "injury_status": m.player.injury_status,
    }
