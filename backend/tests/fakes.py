"""A league and two forecasters built from recorded fixtures.

These exist because Yahoo has our application under review and every Fantasy endpoint
currently answers ``401 additional_authorization_required``. Without them the MCP server
could not be run at all, and "it imports cleanly" is not evidence that a recommendation
comes out the far end.

They are deliberately not mocks. ``FixtureLeague`` implements ``LeagueReader`` and replays
the same recorded JSON through the same parsers the live client uses, so what it exercises
is the real path minus the socket. That is also why ``LeagueReader`` is a Protocol: this is
its second caller (CLAUDE.md 2.0), the first being ``YahooClient``.

Two consumers: ``tests/test_mcp.py`` and ``scripts/mcp_demo.py``.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any

from ff.adapters.yahoo import parse
from ff.domain.models import (
    LeagueSettings,
    Matchup,
    Player,
    PlayerId,
    Projection,
    Roster,
    SourceStatus,
)
from ff.domain.opponent import WinningBid

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"

#: The week the Yahoo fixtures were recorded for. Roster and matchup files are week 2, and
#: asking this league for any other week would be answering with the wrong roster.
FIXTURE_WEEK = 2


def load(name: str, directory: Path = FIXTURES) -> Any:
    return json.loads((directory / name).read_text())


class FixtureLeague:
    """A Yahoo league frozen at the moment the fixtures were recorded."""

    def __init__(
        self,
        my_team_key: str = "470.l.1000.t.3",
        faab: int | None = None,
        noisy_transactions: bool = False,
    ) -> None:
        self.my_team_key = my_team_key
        self._faab_override = faab
        self._transactions_file = (
            "transactions_with_noise.json" if noisy_transactions else "transactions.json"
        )
        self.calls: list[str] = []

    def league_key(self) -> str:
        self.calls.append("league_key")
        return "470.l.1000"

    def team_key(self) -> str:
        self.calls.append("team_key")
        return self.my_team_key

    def league_settings(self) -> LeagueSettings:
        self.calls.append("league_settings")
        return parse.parse_league_settings(load("league_settings.json"))

    def roster(self, week: int, team_key: str | None = None) -> Roster:
        self.calls.append(f"roster:{week}:{team_key or 'me'}")
        mine = team_key is None or team_key == self.my_team_key
        name = "roster_week2.json" if mine else "roster_week2_opponent.json"
        return parse.parse_roster(load(name), week)

    def matchup(self, week: int, team_key: str | None = None) -> Matchup:
        self.calls.append(f"matchup:{week}")
        return parse.parse_matchup(load("matchup_week2.json"), self.my_team_key, week)

    def free_agents(
        self, position: str | None = None, limit: int = 50, status: str = "FA"
    ) -> list[Player]:
        self.calls.append(f"free_agents:{position}:{limit}")
        players = parse.parse_players(load("free_agents.json"))
        if position:
            players = [p for p in players if p.position.value == position]
        return players[:limit]

    def faab_balance(self, team_key: str | None = None) -> int:
        self.calls.append("faab_balance")
        if self._faab_override is not None:
            return self._faab_override
        return parse.parse_faab_balance(load("team_faab.json"))

    def transactions(self, limit: int = 100) -> list[WinningBid]:
        self.calls.append(f"transactions:{limit}")
        return parse.parse_transactions(load(self._transactions_file))[:limit]


class FixtureProjections:
    """A forecaster that answers from a deterministic function of the player id.

    Not recorded numbers: the ESPN and Sleeper fixtures cover a different player set than
    the Yahoo roster fixtures, so joining them would leave most of the roster unprojected
    and the lineup tools with nothing to rank. What is being tested here is the plumbing
    and the shape of the answer, never the accuracy of a projection. Anything that asserts
    on projection *values* belongs in the blend and source tests, which use real recordings.
    """

    def __init__(self, name: str, offset: float = 0.0, required: bool = True) -> None:
        self.name = name
        self.offset = offset
        self.required = required

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        return {p.id: _pseudo_points(p, self.offset) for p in players}

    def status(self) -> SourceStatus:
        return SourceStatus(name=self.name, ok=True, required=self.required, age_seconds=60.0)


def _pseudo_points(player: Player, offset: float) -> float:
    """Stable per-player points, spread over a plausible range for the position."""
    base = {"QB": 18.0, "RB": 11.0, "WR": 10.5, "TE": 7.5, "K": 8.0, "DEF": 7.0}.get(
        player.position.value, 8.0
    )
    # crc32, not hash(): str hashing is salted per process and the demo script should
    # print the same numbers twice in a row.
    spread = (zlib.crc32(str(player.id).encode()) % 1000) / 1000.0
    return round(base * (0.5 + spread), 2) + offset


def fixture_sources() -> list[FixtureProjections]:
    """Two independent-looking sources, because one source is a misconfiguration."""
    return [FixtureProjections("fixture-a"), FixtureProjections("fixture-b", offset=0.8)]


def flat_projection(
    player_id: PlayerId, mean: float, sources: tuple[str, ...] = ("fixture-a",)
) -> Projection:
    return Projection(
        player_id=player_id,
        mean=mean,
        epistemic_sd=1.0,
        aleatoric_sd=max(1.0, mean * 0.4),
        per_source=tuple((name, mean) for name in sources),
    )
