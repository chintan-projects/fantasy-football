"""Entering the league by hand: resolve what was read off a screenshot, then save it.

The rules that make a screenshot safe to decide on:

* **A roster is all or nothing.** If one name cannot be matched to exactly one player,
  nothing is saved and the answer says which name and why. A lineup computed over
  fourteen of fifteen players looks complete and is not.
* **A free-agent list is best effort.** A name that cannot be matched is skipped and
  reported. Missing one free agent costs one option; it does not corrupt the others.
* **Corrected spellings are reported back.** "Puka Nakua" is saved as Puka Nacua, and the
  owner is told so, because the only person who can catch a wrong correction is the one
  looking at the screen.

No math here. ``domain`` decides; this records what it decides on.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ff.adapters.manual import (
    ASK,
    DEFAULT_NUM_TEAMS,
    DEFAULT_SLOTS,
    MAX_AGE_SECONDS,
    Match,
    PlayerDirectory,
    PlayerEntry,
    parse_slot,
    player_record,
    settings_from,
)
from ff.adapters.store import Store
from ff.core.logging import get_logger
from ff.domain.models import Slot

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EntryResult:
    """What happened to one batch of entered players."""

    saved: bool
    matched: list[Match]
    problems: list[str]
    """One plain sentence per entry that could not be used, naming it as it was entered."""
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Bid:
    """One completed FAAB add, as read off the league's transactions page."""

    player: PlayerEntry
    amount: int
    manager: str
    week: int
    mine: bool = False


def enter_roster(
    store: Store,
    directory: PlayerDirectory,
    entries: list[PlayerEntry],
    *,
    week: int,
    whose: Literal["mine", "opponent"],
    opponent_name: str | None = None,
) -> EntryResult:
    """Save my roster, or this week's opponent. Nothing is saved unless every name matched."""
    matches = [directory.resolve(e) for e in entries]
    problems = [match_problem(m) for m in matches if not m.ok]
    problems += _bad_slots(entries)
    problems += _listed_twice(matches)
    if not entries:
        problems.append("No players were sent.")
    if problems:
        return EntryResult(saved=False, matched=matches, problems=problems)

    records = [
        player_record(m.player, parse_slot(m.entry.slot)) for m in matches if m.player is not None
    ]
    kind = "roster" if whose == "mine" else "opponent"
    payload: dict[str, Any] = {"players": records}
    if whose == "opponent":
        payload["name"] = (opponent_name or "").strip() or "opponent"
    store.save_league_input(kind, week, payload)

    warnings = _short_roster(store, len(records)) if whose == "mine" else []
    log.info("roster_entered", whose=whose, week=week, players=len(records))
    return EntryResult(saved=True, matched=matches, problems=[], warnings=warnings)


def enter_free_agents(
    store: Store, directory: PlayerDirectory, entries: list[PlayerEntry], *, week: int
) -> EntryResult:
    """Replace the available-player list. Unmatched names are skipped, not fatal."""
    matches = [directory.resolve(e) for e in entries]
    records = []
    seen: set[str] = set()
    for match in matches:
        if match.player is None or str(match.player.id) in seen:
            continue
        seen.add(str(match.player.id))
        record = player_record(match.player)
        record["percent_rostered"] = match.entry.percent_rostered
        records.append(record)

    problems = [match_problem(m) for m in matches if not m.ok]
    if not records:
        return EntryResult(saved=False, matched=matches, problems=problems or ["No players."])
    store.save_league_input("free_agents", week, {"players": records})
    log.info("free_agents_entered", week=week, players=len(records), skipped=len(problems))
    return EntryResult(saved=True, matched=matches, problems=problems)


def enter_transactions(store: Store, directory: PlayerDirectory, bids: list[Bid]) -> EntryResult:
    """Add completed FAAB bids to the history the bidder estimate learns from."""
    matches = [directory.resolve(b.player) for b in bids]
    rows = [
        {
            "player": player_record(m.player),
            "amount": b.amount,
            "manager": b.manager.strip(),
            "week": b.week,
            "mine": b.mine,
        }
        for b, m in zip(bids, matches, strict=True)
        if m.player is not None
    ]
    problems = [match_problem(m) for m in matches if not m.ok]
    if not rows:
        return EntryResult(saved=False, matched=matches, problems=problems or ["No bids."])
    week = max(b.week for b, m in zip(bids, matches, strict=True) if m.player is not None)
    store.save_league_input("transactions", week, {"bids": rows})
    return EntryResult(saved=True, matched=matches, problems=problems)


def update_league(
    store: Store,
    *,
    week: int,
    faab_remaining: int | None = None,
    faab_budget: int | None = None,
    num_teams: int | None = None,
    playoff_start_week: int | None = None,
    roster_slots: dict[str, int] | None = None,
) -> list[str]:
    """Change the league facts that are typed rather than read. Returns any problems.

    The balance is saved on its own row because it changes weekly and the settings change
    never: keeping them apart means a stale balance can be refused without also refusing
    the roster rules.
    """
    problems: list[str] = []
    changes: dict[str, Any] = {}
    if faab_budget is not None:
        changes["faab_budget"] = faab_budget
    if num_teams is not None:
        changes["num_teams"] = num_teams
    if playoff_start_week is not None:
        changes["playoff_start_week"] = playoff_start_week
    if roster_slots is not None:
        slots: dict[str, int] = {}
        for name, count in roster_slots.items():
            slot = parse_slot(name)
            if slot is None:
                problems.append(f"{name!r} is not a roster slot I know. Nothing was changed.")
            else:
                slots[slot.value] = slots.get(slot.value, 0) + count
        changes["slots"] = slots

    if problems:
        return problems  # all or nothing, the balance included
    if faab_remaining is not None:
        store.save_league_input("faab", week, {"remaining": faab_remaining})
    if changes:
        current = store.latest_league_input("settings")
        merged = {**(current.payload if current else {}), **changes}
        store.save_league_input("settings", week, merged)
    return problems


# ---- what has been entered -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InputState:
    name: str
    entered: bool
    days_old: float | None
    fresh: bool
    detail: str
    ask: str | None


def input_states(store: Store, week: int, now: Callable[[], float] = time.time) -> list[InputState]:
    """Every hand-entered input, how old it is, and what to send if it is missing."""
    states = []
    for kind, name in (
        ("roster", "your roster"),
        ("faab", "FAAB balance"),
        ("free_agents", "available players"),
    ):
        entry = store.latest_league_input(kind)
        age = None if entry is None else now() - entry.entered_at
        fresh = age is not None and age <= MAX_AGE_SECONDS
        detail = "not entered" if entry is None else _describe(kind, entry.payload)
        states.append(
            InputState(
                name, entry is not None, _days(age), fresh, detail, None if fresh else ASK[kind]
            )
        )

    opponent = store.latest_league_input("opponent", week)
    states.append(
        InputState(
            f"week {week} opponent",
            opponent is not None,
            _days(None if opponent is None else now() - opponent.entered_at),
            opponent is not None,
            "not entered" if opponent is None else _describe("opponent", opponent.payload),
            None if opponent is not None else ASK["opponent"].format(week=week),
        )
    )

    settings = store.latest_league_input("settings")
    states.append(
        InputState(
            "league settings",
            settings is not None,
            _days(None if settings is None else now() - settings.entered_at),
            True,
            _describe_settings(settings.payload if settings else None),
            None
            if settings is not None
            else "Confirm your league's roster slots and number of teams, or I'll keep "
            "assuming Yahoo's defaults.",
        )
    )
    bids = store.league_inputs("transactions")
    states.append(
        InputState(
            "league transactions",
            bool(bids),
            None,
            True,
            f"{sum(len(b.payload['bids']) for b in bids)} winning bids entered"
            if bids
            else "none entered -- bids are sized with a league-average guess at competition",
            None,
        )
    )
    return states


def hand_entered_caveats(store: Store, now: Callable[[], float] = time.time) -> list[str]:
    """What every decision in manual mode should say about where its inputs came from."""
    out = []
    roster = store.latest_league_input("roster")
    if roster is not None:
        out.append(
            f"Roster as you sent it {_ago(now() - roster.entered_at)}. If a claim or trade "
            f"has gone through since, send a fresh screenshot."
        )
    faab = store.latest_league_input("faab")
    if faab is not None:
        out.append(
            f"FAAB balance ${faab.payload['remaining']} is the number you gave me "
            f"{_ago(now() - faab.entered_at)}, not read from Yahoo."
        )
    if store.latest_league_input("settings") is None:
        out.append(
            "League settings are assumed, not confirmed: Yahoo's default roster "
            f"(QB, 2 RB, 3 WR, TE, FLEX, K, DEF) and {DEFAULT_NUM_TEAMS} teams."
        )
    return out


# ---- wording ---------------------------------------------------------------------


def match_problem(match: Match) -> str:
    seen = _as_entered(match)
    if match.how == "ambiguous":
        options = "; ".join(
            f"{p.name} ({p.position.value}, {p.team or 'no team'})" for p in match.options
        )
        return f"{seen} could be more than one player: {options}. Send the position and team."
    return f"{seen} doesn't match any player I know. Check the spelling against the screen."


def _as_entered(match: Match) -> str:
    entry = match.entry
    extra = ", ".join(x for x in (entry.position, entry.team) if x)
    return f"'{entry.name}'" + (f" ({extra})" if extra else "")


def _bad_slots(entries: list[PlayerEntry]) -> list[str]:
    return [
        f"'{e.name}' is in slot {e.slot!r}, which isn't a roster slot I know."
        for e in entries
        if e.slot and parse_slot(e.slot) is None
    ]


def _listed_twice(matches: list[Match]) -> list[str]:
    seen: dict[str, str] = {}
    problems = []
    for match in matches:
        if match.player is None:
            continue
        key = str(match.player.id)
        if key in seen:
            problems.append(
                f"'{seen[key]}' and '{match.entry.name}' are the same player, "
                f"{match.player.name}. One of them was probably misread."
            )
        seen[key] = match.entry.name
    return problems


def _short_roster(store: Store, count: int) -> list[str]:
    settings = store.latest_league_input("settings")
    slots = settings_from(settings.payload if settings else {}, "", 100).slot_counts
    expected = sum(n for slot, n in slots.items() if slot is not Slot.IR)
    if count >= expected:
        return []
    return [
        f"Only {count} players, and a full roster under the league settings I have is "
        f"{expected}. If the screenshot cut off the bench, send the whole roster again in "
        f"one go -- each send replaces the last. If your league has fewer roster spots, "
        f"tell me its slots."
    ]


def _describe(kind: str, payload: dict[str, Any]) -> str:
    if kind == "faab":
        return f"${payload['remaining']} left"
    if kind == "opponent":
        return f"{payload.get('name', 'opponent')}, {len(payload['players'])} players"
    return f"{len(payload['players'])} players"


def _describe_settings(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return (
            f"assumed: Yahoo's default roster and {DEFAULT_NUM_TEAMS} teams -- "
            f"{', '.join(f'{n} {s.value}' for s, n in DEFAULT_SLOTS.items())}"
        )
    slots = payload.get("slots")
    described = ", ".join(f"{n} {s}" for s, n in slots.items()) if slots else "default slots"
    teams = payload.get("num_teams", DEFAULT_NUM_TEAMS)
    return f"{teams} teams; {described}"


def _days(age: float | None) -> float | None:
    return None if age is None else round(age / 86400, 1)


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return "within the last hour"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"
