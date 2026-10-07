"""Manual league mode: the league as the owner reports it, not as Yahoo's API reports it.

Yahoo has not approved the API application (docs/YAHOO_SETUP.md), and the season does not
wait. Everything the decisions need from Yahoo -- the roster, this week's opponent, the
available players and the FAAB balance -- is on screen in the Yahoo app. In this mode the
owner sends a screenshot, Claude reads the players off it, and they land here.

Two parts:

* ``PlayerDirectory`` turns a name as it appears on screen ("J. Allen", "Broncos D/ST",
  "Amon-Ra St Brown") into one player, or says why it cannot. It never picks between two
  candidates: "B. Robinson, RB, ATL" is Bijan or Brian, and a lineup built on the wrong
  one is worse than a question.
* ``ManualLeague`` implements ``LeagueReader`` over what was entered, so every decision
  tool runs unchanged. It refuses an input too old to trust rather than deciding on it,
  and its refusal is the instruction for fixing it.

Player ids are Sleeper's. Projection sources join on name and position, never on id, so
the id only has to be stable -- and Sleeper's player list is free, complete and the same
list the projections come from.
"""

from __future__ import annotations

import difflib
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ff.adapters._common import canonical_team, normalize_name, parse_position
from ff.adapters.store import LeagueInput, Store
from ff.core.errors import MissingInput
from ff.domain.models import (
    LeagueKey,
    LeagueSettings,
    Matchup,
    Player,
    PlayerId,
    Position,
    Roster,
    RosterSpot,
    Slot,
    TeamKey,
)
from ff.domain.opponent import WinningBid

#: The owner's own team, in a league with no Yahoo team keys.
MY_TEAM = "me"

#: An entry older than this is refused. A week is one waiver cycle: by the next Tuesday the
#: roster, the free-agent pool and the budget have all had a chance to change.
MAX_AGE_SECONDS = 7 * 24 * 3600

#: Yahoo's default roster, used until the owner says otherwise. Reported as assumed
#: everywhere it is used, because a wrong slot count changes which lineup is legal.
DEFAULT_SLOTS: dict[Slot, int] = {
    Slot.QB: 1,
    Slot.WR: 3,
    Slot.RB: 2,
    Slot.TE: 1,
    Slot.FLEX: 1,
    Slot.K: 1,
    Slot.DEF: 1,
    Slot.BENCH: 6,
    Slot.IR: 1,
}
DEFAULT_NUM_TEAMS = 12
DEFAULT_PLAYOFF_START = 15

#: How each piece is asked for when it is missing. The reader is on a phone, so each one
#: names the screen in the Yahoo app rather than a setting.
ASK = {
    "roster": (
        "I don't have your roster. Send a screenshot of your team page in the Yahoo app "
        "(My Team) and I'll read the players off it."
    ),
    "opponent": (
        "I don't know who you're playing in week {week}. Send a screenshot of this week's "
        "matchup page in the Yahoo app -- it shows their starters."
    ),
    "free_agents": (
        "I don't have a list of available players. In the Yahoo app open Players, keep the "
        "filter on available players, and send a screenshot or two of the top of the list."
    ),
    "faab": (
        "I don't know how much FAAB you have left. It's on your team page in the Yahoo "
        "app. Just tell me the number."
    ),
}

STALE = (
    "Your {what} was last sent {days} days ago, and a waiver run has happened since, so "
    "it may be wrong. Send a fresh one: {how}"
)

#: Words that mark a team defense on screen: "D/ST", "DST", "Defense".
#: "D/ST" normalizes to the two tokens "d st", and is only a defense marker as a pair:
#: "st" on its own ends "Amon-Ra St. Brown".
_DEFENSE_WORDS = frozenset({"dst", "def", "defense", "defence"})

#: How close a misread name has to be to count: one or two wrong letters in a full name,
#: "Puka Nakua" for "Puka Nacua". Punctuation and suffixes never get this far -- they are
#: normalized away first. A close match is always reported back to the owner.
_CLOSE_CUTOFF = 0.85


# ---- matching names --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlayerEntry:
    """One player as read off a screen. Everything but the name is optional."""

    name: str
    position: str | None = None
    team: str | None = None
    slot: str | None = None
    percent_rostered: float | None = None


@dataclass(frozen=True, slots=True)
class Match:
    """What one entry resolved to, and how sure that is.

    ``how`` is one of ``exact``, ``initial`` (an abbreviated first name), ``close`` (a
    misspelling corrected -- always shown to the owner), ``defense``, ``ambiguous`` or
    ``unknown``. The last two carry no player.
    """

    entry: PlayerEntry
    player: Player | None
    how: str
    options: tuple[Player, ...] = ()

    @property
    def ok(self) -> bool:
        return self.player is not None


class PlayerDirectory:
    """Every player a fantasy roster can hold, searchable the way names appear on screen."""

    def __init__(self, players: Iterable[Player]) -> None:
        self._by_name: dict[str, list[Player]] = defaultdict(list)
        self._defenses: dict[str, list[Player]] = defaultdict(list)
        for player in players:
            if player.position is Position.DEF:
                for alias in _defense_aliases(player):
                    self._defenses[alias].append(player)
            else:
                self._by_name[normalize_name(player.name)].append(player)

    def resolve(self, entry: PlayerEntry) -> Match:
        position = parse_position(entry.position) if entry.position else None
        team = canonical_team(entry.team) if entry.team else ""
        if position is Position.DEF or (position is None and self._looks_like_defense(entry)):
            return self._resolve_defense(entry, team)

        norm = normalize_name(entry.name)
        found, how = list(self._by_name.get(norm, [])), "exact"
        if not _narrow(found, position, team):
            found, how = self._by_initial(norm), "initial"
        if not _narrow(found, position, team):
            found, how = self._close(norm), "close"

        found = _narrow(found, position, team)
        if len(found) == 1:
            return Match(entry, found[0], how)
        if not found:
            return Match(entry, None, "unknown")
        return Match(entry, None, "ambiguous", tuple(found[:5]))

    def _by_initial(self, norm: str) -> list[Player]:
        """ "j allen" -> every player whose first name starts with j and last name is allen.

        The Yahoo mobile app abbreviates first names in matchup and roster views. Scanned
        linearly rather than indexed: a few thousand names, fifteen lookups, once a week.
        """
        initial, _, rest = norm.partition(" ")
        if len(initial) != 1 or not rest:
            return []
        suffix = f" {rest}"
        return [
            player
            for name, players in self._by_name.items()
            if name.startswith(initial) and name.endswith(suffix)
            for player in players
        ]

    def _close(self, norm: str) -> list[Player]:
        names = difflib.get_close_matches(norm, self._by_name, n=3, cutoff=_CLOSE_CUTOFF)
        return [player for name in names for player in self._by_name[name]]

    def _looks_like_defense(self, entry: PlayerEntry) -> bool:
        norm = normalize_name(entry.name)
        stripped = _strip_defense_words(norm)
        if stripped != norm:
            return True
        return stripped in self._defenses and stripped not in self._by_name

    def _resolve_defense(self, entry: PlayerEntry, team: str) -> Match:
        if team and len(self._defenses.get(team.lower(), [])) == 1:
            return Match(entry, self._defenses[team.lower()][0], "defense")
        found = self._defenses.get(_strip_defense_words(normalize_name(entry.name)), [])
        if len(found) == 1:
            return Match(entry, found[0], "defense")
        if not found:
            return Match(entry, None, "unknown")
        return Match(entry, None, "ambiguous", tuple(found))


def _narrow(found: list[Player], position: Position | None, team: str) -> list[Player]:
    """Cut candidates down by what else the screen said.

    Position is a hard filter. Team is soft: a player traded this week shows his new team
    in Yahoo before every other source catches up, so a team mismatch never turns a lone
    match into no match. Players on an NFL roster beat players with no team, because a
    retired player sharing a name is not who is on a fantasy roster in October.
    """
    if position is not None:
        found = [p for p in found if p.position is position]
    if team:
        same_team = [p for p in found if p.team == team]
        found = same_team or found
    on_a_team = [p for p in found if p.team]
    return on_a_team or found


def _defense_aliases(player: Player) -> set[str]:
    """ "Buffalo Bills", "BUF" -> buffalo bills, bills, buffalo, buf.

    The city alone is ambiguous for Los Angeles and New York; both teams are indexed under
    it and the resolver reports the ambiguity instead of choosing.
    """
    full = normalize_name(player.name)
    tokens = full.split(" ")
    aliases = {full, tokens[-1], " ".join(tokens[:-1])}
    if player.team:
        aliases.add(player.team.lower())
    return {a for a in aliases if a}


def _strip_defense_words(norm: str) -> str:
    tokens = norm.split(" ")
    while tokens:
        if tokens[-1] in _DEFENSE_WORDS:
            tokens.pop()
        elif tokens[-2:] == ["d", "st"]:
            del tokens[-2:]
        else:
            break
    return " ".join(tokens)


def parse_slot(raw: str | None) -> Slot | None:
    """A roster slot as Yahoo labels it on screen, or None if it is not one."""
    if not raw:
        return None
    cleaned = raw.strip().upper().replace(" ", "")
    aliases = {
        "FLEX": Slot.FLEX,
        "W/R/T": Slot.FLEX,
        "WR/RB/TE": Slot.FLEX,
        "SUPERFLEX": Slot.SUPERFLEX,
        "Q/W/R/T": Slot.SUPERFLEX,
        "BENCH": Slot.BENCH,
        "BN": Slot.BENCH,
        "D/ST": Slot.DEF,
        "DST": Slot.DEF,
    }
    if cleaned in aliases:
        return aliases[cleaned]
    try:
        return Slot(cleaned)
    except ValueError:
        return None


# ---- storing players -------------------------------------------------------------


def player_record(player: Player, slot: Slot | None = None) -> dict[str, Any]:
    """A player as stored. Everything needed to rebuild him without the directory."""
    return {
        "id": str(player.id),
        "name": player.name,
        "position": player.position.value,
        "team": player.team,
        "slots": sorted(s.value for s in player.eligible_slots),
        "injury_status": player.injury_status,
        "percent_rostered": player.percent_rostered,
        "slot": slot.value if slot else None,
    }


def player_from(record: dict[str, Any]) -> Player:
    return Player(
        id=PlayerId(str(record["id"])),
        name=str(record["name"]),
        position=Position(record["position"]),
        team=str(record.get("team") or ""),
        eligible_slots=frozenset(Slot(s) for s in record.get("slots", [])),
        injury_status=record.get("injury_status"),
        percent_rostered=record.get("percent_rostered"),
    )


def _spots(
    records: list[dict[str, Any]], default: Callable[[Player], Slot]
) -> tuple[RosterSpot, ...]:
    spots = []
    for record in records:
        player = player_from(record)
        slot = Slot(record["slot"]) if record.get("slot") else default(player)
        spots.append(RosterSpot(player=player, slot=slot))
    return tuple(spots)


def _own_slot(player: Player) -> Slot:
    """An opponent player listed with no slot was listed because he is starting."""
    return Slot(player.position.value)


# ---- the league ------------------------------------------------------------------


def settings_from(payload: dict[str, Any], league_key: str, faab_budget: int) -> LeagueSettings:
    raw_slots = payload.get("slots")
    slots = {Slot(k): int(v) for k, v in raw_slots.items()} if raw_slots else dict(DEFAULT_SLOTS)
    return LeagueSettings(
        league_key=LeagueKey(league_key),
        slot_counts=slots,
        uses_faab=True,
        faab_budget=int(payload.get("faab_budget") or faab_budget),
        playoff_start_week=int(payload.get("playoff_start_week") or DEFAULT_PLAYOFF_START),
        num_teams=int(payload.get("num_teams") or DEFAULT_NUM_TEAMS),
    )


class ManualLeague:
    """A ``LeagueReader`` over what the owner has entered.

    It reads only from the store. Matching names to players happened when they were
    entered, so a decision never depends on the player directory being reachable.
    """

    def __init__(
        self,
        store: Store,
        *,
        league_key: str = "",
        faab_budget: int = 100,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self._league_key = league_key
        self._faab_budget = faab_budget
        self._clock = clock

    def league_key(self) -> str:
        return self._league_key or "manual"

    def team_key(self) -> str:
        return MY_TEAM

    def league_settings(self) -> LeagueSettings:
        entry = self.store.latest_league_input("settings")
        payload = entry.payload if entry else {}
        return settings_from(payload, self.league_key(), self._faab_budget)

    def roster(self, week: int, team_key: str | None = None) -> Roster:
        if team_key is None or team_key == MY_TEAM:
            entry = self._fresh("roster", "roster", "a screenshot of your team page.")
            return Roster(TeamKey(MY_TEAM), week, _spots(entry.payload["players"], _bench))
        entry = self._opponent(week)
        return Roster(TeamKey(team_key), week, _spots(entry.payload["players"], _own_slot))

    def matchup(self, week: int, team_key: str | None = None) -> Matchup:
        entry = self._opponent(week)
        roster = _spots(entry.payload["players"], _own_slot)
        name = entry.payload.get("name") or "opponent"
        return Matchup(
            week=week,
            my_team=TeamKey(MY_TEAM),
            opponent=TeamKey(f"opponent:{name}"),
            opponent_starters=tuple(
                s.player.id for s in roster if s.slot not in (Slot.BENCH, Slot.IR)
            ),
        )

    def free_agents(
        self, position: str | None = None, limit: int = 50, status: str = "FA"
    ) -> list[Player]:
        """The list as entered, minus anyone the owner has since added to their roster."""
        entry = self._fresh(
            "free_agents", "list of available players", "a screenshot of Players in Yahoo."
        )
        mine = self.store.latest_league_input("roster")
        owned = {r["id"] for r in mine.payload["players"]} if mine else set()
        players = [player_from(r) for r in entry.payload["players"] if r["id"] not in owned]
        if position:
            players = [p for p in players if p.position.value == position]
        return players[:limit]

    def faab_balance(self, team_key: str | None = None) -> int:
        entry = self._fresh("faab", "FAAB balance", "just tell me the number.")
        return int(entry.payload["remaining"])

    def transactions(self, limit: int = 100) -> list[WinningBid]:
        """Every winning bid ever entered, de-duplicated, newest week first.

        Entered transactions overlap -- the same Yahoo page gets sent twice -- so the same
        manager buying the same player for the same amount in the same week counts once.
        """
        seen: set[tuple[str, str, int, int]] = set()
        bids: list[WinningBid] = []
        for entry in self.store.league_inputs("transactions"):
            for row in entry.payload["bids"]:
                team = MY_TEAM if row.get("mine") else f"manager:{row['manager']}"
                player = player_from(row["player"])
                key = (team, str(player.id), int(row["week"]), int(row["amount"]))
                if key in seen:
                    continue
                seen.add(key)
                bids.append(
                    WinningBid(
                        team_key=TeamKey(team),
                        player_id=str(player.id),
                        position=player.position,
                        amount=int(row["amount"]),
                        week=int(row["week"]),
                        timestamp=entry.entered_at,
                    )
                )
        bids.sort(key=lambda b: (b.week, b.timestamp), reverse=True)
        return bids[:limit]

    # ---- freshness ---------------------------------------------------------------

    def _fresh(self, kind: str, what: str, how: str) -> LeagueInput:
        entry = self.store.latest_league_input(kind)
        if entry is None:
            raise MissingInput(ASK[kind])
        age = self._clock() - entry.entered_at
        if age > MAX_AGE_SECONDS:
            raise MissingInput(STALE.format(what=what, days=int(age // 86400), how=how))
        return entry

    def _opponent(self, week: int) -> LeagueInput:
        entry = self.store.latest_league_input("opponent", week)
        if entry is None:
            raise MissingInput(ASK["opponent"].format(week=week))
        return entry


def _bench(player: Player) -> Slot:
    """My players listed without a slot. The slot is cosmetic: the recommendation
    considers every rostered player for every slot he is eligible for."""
    return Slot.BENCH
