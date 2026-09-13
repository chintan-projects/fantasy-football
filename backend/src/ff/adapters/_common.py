"""Normalization every adapter needs, in one place.

No source speaks Yahoo's player keys. ESPN has its own integer ids, FantasyPros has
another set, nflverse keys on GSIS ids. Joining them means matching on name, position and
team -- so the matching rule lives here rather than being re-derived, slightly differently,
in each adapter. That is CLAUDE.md 2.1: if two providers need the same normalization, it
goes in ``adapters/_common.py``.

Three callers today: espn, fantasypros, nflverse.
"""

from __future__ import annotations

import re
import unicodedata

from ff.domain.models import Position

#: ESPN's numeric proTeamId, read from
#: https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams on 2026-09-12.
#: Values are ESPN's own abbreviations and still need canonical_team() applied.
ESPN_TEAM_BY_ID: dict[int, str] = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WSH",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}

#: ESPN's defaultPositionId. Verified against a live kona_player_info response.
ESPN_POSITION_BY_ID: dict[int, Position] = {
    1: Position.QB,
    2: Position.RB,
    3: Position.WR,
    4: Position.TE,
    5: Position.K,
    16: Position.DEF,
}

#: The same franchise under four different abbreviations across four sources. Yahoo's
#: spelling is the canonical one, because Yahoo is the system of record for the roster.
_TEAM_ALIASES: dict[str, str] = {
    "WSH": "WAS",
    "WFT": "WAS",
    "JAC": "JAX",
    "LA": "LAR",
    "SD": "LAC",
    "OAK": "LV",
    "STL": "LAR",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "GNB": "GB",
    "KAN": "KC",
    "NWE": "NE",
    "NOR": "NO",
    "SFO": "SF",
    "TAM": "TB",
    "LVR": "LV",
}

#: Generational suffixes are inconsistently present across sources and never disambiguate.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_NON_LETTER = re.compile(r"[^a-z ]")
_SPACES = re.compile(r"\s+")


def canonical_team(abbreviation: str | None) -> str:
    if not abbreviation:
        return ""
    upper = abbreviation.strip().upper()
    return _TEAM_ALIASES.get(upper, upper)


def normalize_name(name: str) -> str:
    """Strip everything that varies between sources and nothing that identifies a player.

    Accents, punctuation and generational suffixes all differ source to source --
    "Amon-Ra St. Brown" appears with and without the hyphen and the period. None of them
    ever distinguish two real players, so all of them come out.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    lowered = _NON_LETTER.sub(" ", ascii_only.lower().replace("'", "").replace(".", ""))
    parts = [p for p in _SPACES.sub(" ", lowered).strip().split(" ") if p]
    while len(parts) > 2 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def match_key(name: str, position: Position, team: str | None = None) -> str:
    """The join key between any two sources.

    Team defenses match on team, not on name: Yahoo says "Denver Broncos", ESPN says
    "Broncos D/ST", and nobody says the same thing twice. There is exactly one defense per
    team, so the team abbreviation is both sufficient and unambiguous.

    Everyone else matches on position plus normalized name. Team is deliberately *not*
    part of the key for players, because a player traded or signed mid-week is the same
    player and the sources update their team field on different days.
    """
    if position is Position.DEF:
        return f"DEF:{canonical_team(team)}"
    return f"{position.value}:{normalize_name(name)}"
