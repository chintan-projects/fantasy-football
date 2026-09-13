"""Turning Yahoo's JSON into domain types.

Yahoo's ``?format=json`` is a mechanical transliteration of its XML, and it is hostile in
three specific ways (see .claude/skills/yahoo-fantasy-api section 2):

1. Repeated elements become an object keyed ``"0"``, ``"1"``, ... with a ``"count"``
   sibling. They are not arrays.
2. One entity is split across a list of single-key dicts that you have to merge back
   together.
3. Sub-resources are interleaved as positional siblings, so ``/players/percent_owned`` puts
   the player at index i and the ownership at index i+1.

The parsers below are written against *structure*, not against positions. Index-based
parsing works until Yahoo adds a field, and it fails silently when it breaks; a key-based
walk either finds what it needs or raises SchemaDrift with the reason. The same choice
makes quirk 3 disappear -- merging a positional pair is the same operation as merging any
other split entity.

Nothing here does I/O. It takes parsed JSON and returns domain objects.
"""

from __future__ import annotations

from typing import Any

from ff.core.errors import SchemaDrift
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

#: Yahoo position strings that are not exactly our enum names.
_POSITION_ALIASES: dict[str, Position] = {
    "DST": Position.DEF,
    "D/ST": Position.DEF,
    "D": Position.DEF,
    "PK": Position.K,
}

#: Yahoo slot strings that are not exactly our enum values.
_SLOT_ALIASES: dict[str, Slot] = {
    "W/R": Slot.FLEX,
    "W/T": Slot.FLEX,
    "R/W/T": Slot.FLEX,
    "Q/W/R/T": Slot.SUPERFLEX,
    "DST": Slot.DEF,
    "D/ST": Slot.DEF,
}

#: Roster slots that hold a player without starting them.
_NON_STARTING = {"BN", "IR", "IR+", "NA"}


def merge(node: Any) -> dict[str, Any]:
    """Flatten Yahoo's split entity into one dict.

    An entity arrives as a list of single-key dicts, sometimes nested one level deeper, and
    sometimes with an empty list wedged in. Merging left to right rebuilds it. First value
    wins, because the entity's own fields come before any sub-resource that repeats them.
    """
    out: dict[str, Any] = {}
    queue: list[Any] = [node]
    while queue:
        current = queue.pop(0)
        if isinstance(current, list):
            queue = list(current) + queue
        elif isinstance(current, dict):
            for key, value in current.items():
                if key not in out:
                    out[key] = value
    return out


def numbered(node: Any) -> list[Any]:
    """Read Yahoo's "0", "1", "2" pseudo-array back into a real list."""
    if not isinstance(node, dict):
        return []
    keys = sorted((k for k in node if k.isdigit()), key=int)
    return [node[k] for k in keys]


def find(node: Any, key: str) -> Any | None:
    """First value stored under ``key`` anywhere in the tree, breadth first."""
    queue: list[Any] = [node]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            if key in current:
                return current[key]
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return None


def require(node: Any, key: str, what: str) -> Any:
    value = find(node, key)
    if value is None:
        raise SchemaDrift("yahoo", f"{what}: no '{key}' in the response", required=True)
    return value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def parse_game_id(payload: Any) -> str:
    """The current NFL game id, resolved from /game/nfl. Never hardcode this.

    461 was 2025. It changes every season and a stale one produces 400s that read like an
    auth problem.
    """
    game_id = find(payload, "game_id")
    if game_id is None:
        raise SchemaDrift("yahoo", "/game/nfl returned no game_id", required=True)
    return str(game_id)


def parse_position(raw: Any) -> Position | None:
    """Yahoo's display_position can be multi-position ("RB,WR"). Take the first we know."""
    if raw is None:
        return None
    for token in str(raw).split(","):
        token = token.strip().upper()
        if token in _POSITION_ALIASES:
            return _POSITION_ALIASES[token]
        try:
            return Position(token)
        except ValueError:
            continue
    return None


def parse_slot(raw: Any) -> Slot | None:
    if raw is None:
        return None
    token = str(raw).strip().upper()
    if token in _SLOT_ALIASES:
        return _SLOT_ALIASES[token]
    try:
        return Slot(token)
    except ValueError:
        return None


def parse_league_settings(payload: Any) -> LeagueSettings:
    """/league/{key}/settings -> LeagueSettings.

    ``uses_faab`` is read here and checked before any bid is ever built. A league on waiver
    priority rather than FAAB will silently ignore a faab_bid.
    """
    league_key = require(payload, "league_key", "league settings")
    positions = require(payload, "roster_positions", "league settings")

    slot_counts: dict[Slot, int] = {}
    for entry in positions if isinstance(positions, list) else numbered(positions):
        row = merge(entry)
        # Each entry is wrapped one level deeper: {"roster_position": {"position": ...}}.
        if "roster_position" in row:
            row = merge(row["roster_position"])
        raw_position = row.get("position")
        if raw_position is None:
            continue
        count = _as_int(row.get("count"), 1)
        if str(raw_position).upper() in _NON_STARTING:
            continue
        slot = parse_slot(raw_position)
        if slot is None:
            # An unrecognized starting slot would silently shrink the lineup, so say so.
            raise SchemaDrift(
                "yahoo",
                f"league settings: unknown roster position '{raw_position}'",
                required=True,
            )
        slot_counts[slot] = slot_counts.get(slot, 0) + count

    if not slot_counts:
        raise SchemaDrift("yahoo", "league settings: no starting slots", required=True)

    uses_faab = _as_bool(find(payload, "uses_faab"))
    return LeagueSettings(
        league_key=LeagueKey(str(league_key)),
        slot_counts=slot_counts,
        uses_faab=uses_faab,
        # Yahoo does not publish the starting budget on /settings; it is per-team and lives
        # on the team's faab_balance. 100 is the Yahoo default and gets corrected from the
        # live balance before any bid is submitted.
        faab_budget=_as_int(find(payload, "faab_budget"), 100),
        playoff_start_week=_as_int(find(payload, "playoff_start_week"), 15),
        num_teams=_as_int(find(payload, "num_teams"), 0),
    )


def parse_player(node: Any) -> Player:
    """One player, from anywhere a player appears (roster, free agents, matchup)."""
    row = merge(node)
    player_key = row.get("player_key")
    if player_key is None:
        raise SchemaDrift("yahoo", "player has no player_key", required=True)

    name = row.get("name")
    full_name = name.get("full") if isinstance(name, dict) else None

    position = parse_position(row.get("display_position") or row.get("primary_position"))
    if position is None:
        raise SchemaDrift(
            "yahoo",
            f"player {player_key}: unreadable position "
            f"{row.get('display_position') or row.get('primary_position')!r}",
            required=True,
        )

    eligible: set[Slot] = set()
    raw_eligible = row.get("eligible_positions")
    for entry in raw_eligible if isinstance(raw_eligible, list) else numbered(raw_eligible):
        slot = parse_slot(merge(entry).get("position"))
        if slot is not None:
            eligible.add(slot)
    if not eligible:
        eligible = {slot for slot in Slot if slot.value == position.value}

    percent = find(row.get("percent_owned"), "value")

    return Player(
        id=PlayerId(str(player_key)),
        name=str(full_name or player_key),
        position=position,
        team=str(row.get("editorial_team_abbr") or ""),
        eligible_slots=frozenset(eligible),
        is_undroppable=_as_bool(row.get("is_undroppable")),
        injury_status=str(row["status"]) if row.get("status") else None,
        percent_rostered=float(percent) if percent is not None else None,
    )


def parse_players(payload: Any) -> list[Player]:
    """Every player in a /players collection, in Yahoo's order."""
    collection = find(payload, "players")
    if collection is None:
        raise SchemaDrift("yahoo", "no 'players' collection in the response", required=True)
    out: list[Player] = []
    for entry in numbered(collection):
        player = find(entry, "player")
        if player is not None:
            out.append(parse_player(player))
    return out


def parse_roster(payload: Any, week: int) -> Roster:
    """/team/{key}/roster;week={w} -> Roster, with each player's current slot.

    NFL rosters are addressed by week, not by date.
    """
    team_key = require(payload, "team_key", "roster")
    collection = find(payload, "players")
    if collection is None:
        raise SchemaDrift("yahoo", "roster has no 'players' collection", required=True)

    spots: list[RosterSpot] = []
    for entry in numbered(collection):
        node = find(entry, "player")
        if node is None:
            continue
        player = parse_player(node)
        raw_slot = find(merge(node).get("selected_position"), "position")
        slot = parse_slot(raw_slot) or Slot.BENCH
        spots.append(RosterSpot(player=player, slot=slot))

    if not spots:
        raise SchemaDrift("yahoo", "roster came back empty", required=True)
    return Roster(team_key=TeamKey(str(team_key)), week=week, spots=tuple(spots))


def parse_matchup(payload: Any, my_team_key: str, week: int) -> Matchup:
    """/team/{key}/matchups;weeks={w} -> who I play and who they start.

    Yahoo returns both teams in the matchup; which one is "mine" is decided by team_key,
    never by position in the list.
    """
    matchups = find(payload, "matchups")
    if matchups is None:
        raise SchemaDrift("yahoo", "no 'matchups' in the response", required=True)

    for entry in numbered(matchups):
        node = find(entry, "matchup")
        if node is None:
            continue
        teams = find(node, "teams")
        parsed = [merge(find(t, "team")) for t in numbered(teams) if find(t, "team") is not None]
        keys = [str(t.get("team_key")) for t in parsed]
        if my_team_key not in keys:
            continue
        opponent = next((t for t in parsed if str(t.get("team_key")) != my_team_key), None)
        if opponent is None:
            raise SchemaDrift("yahoo", f"week {week} matchup has no opponent", required=True)
        starters = tuple(
            PlayerId(str(p.get("player_key")))
            for p in (merge(find(e, "player")) for e in numbered(find(opponent, "players")))
            if p.get("player_key") is not None
        )
        return Matchup(
            week=week,
            my_team=TeamKey(my_team_key),
            opponent=TeamKey(str(opponent.get("team_key"))),
            opponent_starters=starters,
        )

    raise SchemaDrift(
        "yahoo", f"no week {week} matchup found for team {my_team_key}", required=True
    )
