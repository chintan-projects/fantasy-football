"""Pure domain types. No I/O, no clock, no env — see CLAUDE.md 2.2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import NewType

PlayerId = NewType("PlayerId", str)
TeamKey = NewType("TeamKey", str)
LeagueKey = NewType("LeagueKey", str)
TransactionKey = NewType("TransactionKey", str)


class Position(StrEnum):
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DEF = "DEF"


class Slot(StrEnum):
    """Yahoo roster slot names. Must match /league/settings roster_positions exactly."""

    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    FLEX = "W/R/T"
    SUPERFLEX = "Q/W/R/T"
    K = "K"
    DEF = "DEF"
    BENCH = "BN"
    IR = "IR"


#: Which positions may fill each slot. These sets are nested (FLEX contains RB/WR/TE),
#: which is what makes lineup assembly a matroid and greedy provably optimal.
#: See .claude/skills/fantasy-decision-math, section 2.
SLOT_ELIGIBILITY: dict[Slot, frozenset[Position]] = {
    Slot.QB: frozenset({Position.QB}),
    Slot.RB: frozenset({Position.RB}),
    Slot.WR: frozenset({Position.WR}),
    Slot.TE: frozenset({Position.TE}),
    Slot.FLEX: frozenset({Position.RB, Position.WR, Position.TE}),
    Slot.SUPERFLEX: frozenset({Position.QB, Position.RB, Position.WR, Position.TE}),
    Slot.K: frozenset({Position.K}),
    Slot.DEF: frozenset({Position.DEF}),
}

STARTING_SLOTS: frozenset[Slot] = frozenset(SLOT_ELIGIBILITY)


@dataclass(frozen=True, slots=True)
class Player:
    id: PlayerId
    name: str
    position: Position
    team: str
    eligible_slots: frozenset[Slot]
    is_undroppable: bool = False
    injury_status: str | None = None
    practice_status: str | None = None
    percent_rostered: float | None = None

    def can_fill(self, slot: Slot) -> bool:
        if slot in (Slot.BENCH, Slot.IR):
            return True
        return slot in self.eligible_slots


@dataclass(frozen=True, slots=True)
class Projection:
    """A player-week forecast with both kinds of uncertainty kept separate.

    ``epistemic_sd`` is disagreement between forecasters (we do not know his role).
    ``aleatoric_sd`` is week-to-week volatility (touchdowns are Poisson-ish).
    They are different things and only the second one is what "ceiling/floor" means.
    """

    player_id: PlayerId
    mean: float
    epistemic_sd: float
    aleatoric_sd: float
    p_zero: float = 0.0
    per_source: tuple[tuple[str, float], ...] = ()
    """What each source actually said, sorted by name.

    The ensemble mean is the only number the recommendation uses, but it is not the only
    number worth keeping: CLAUDE.md 2.5 asks for MAE *per source*, and once these are
    averaged away that question can never be answered for this week. They are carried
    here rather than recomputed later because by next week every source has changed its
    mind, and a source cannot be graded on a forecast it no longer admits to making.
    """

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.per_source)

    @property
    def total_sd(self) -> float:
        return float((self.epistemic_sd**2 + self.aleatoric_sd**2) ** 0.5)


@dataclass(frozen=True, slots=True)
class RosterSpot:
    player: Player
    slot: Slot


@dataclass(frozen=True, slots=True)
class Roster:
    team_key: TeamKey
    week: int
    spots: tuple[RosterSpot, ...]

    @property
    def players(self) -> tuple[Player, ...]:
        return tuple(s.player for s in self.spots)

    @property
    def starters(self) -> tuple[RosterSpot, ...]:
        return tuple(s for s in self.spots if s.slot in STARTING_SLOTS)


@dataclass(frozen=True, slots=True)
class LeagueSettings:
    league_key: LeagueKey
    slot_counts: dict[Slot, int]
    uses_faab: bool
    faab_budget: int
    playoff_start_week: int
    num_teams: int

    @property
    def starting_slots(self) -> tuple[Slot, ...]:
        """Every starting slot, one entry per available seat."""
        out: list[Slot] = []
        for slot, n in self.slot_counts.items():
            if slot in STARTING_SLOTS:
                out.extend([slot] * n)
        return tuple(out)


@dataclass(frozen=True, slots=True)
class LineupPlan:
    """An assignment of players to starting slots, plus the reasoning behind it."""

    week: int
    assignments: tuple[tuple[PlayerId, Slot], ...]
    expected_points: float
    win_probability: float
    win_probability_band: tuple[float, float]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Matchup:
    week: int
    my_team: TeamKey
    opponent: TeamKey
    opponent_starters: tuple[PlayerId, ...]


class Confidence(StrEnum):
    CLEAR = "clear"
    LEAN = "lean"
    TOO_CLOSE = "too_close_to_call"


@dataclass(frozen=True, slots=True)
class SlotDecision:
    """One contested starting slot, with an honest verdict.

    ``TOO_CLOSE`` is a valid and common answer. See CLAUDE.md section 3.
    """

    slot: Slot
    winner: PlayerId
    runner_up: PlayerId | None
    win_prob_delta: float
    confidence: Confidence
    reason: str


@dataclass(frozen=True, slots=True)
class WaiverTarget:
    player: Player
    ros_vorp_per_week: float
    weeks_remaining: int
    interested_bidders: int
    trending_adds: int | None = None


@dataclass(frozen=True, slots=True)
class BidRecommendation:
    player_id: PlayerId
    reservation_value: int
    recommended_bid: int
    shading_for_competition: float
    shading_for_winners_curse: float
    option_value_penalty: float
    reason: str
    drop_candidate: PlayerId | None = None


@dataclass(frozen=True, slots=True)
class SourceStatus:
    name: str
    ok: bool
    required: bool
    age_seconds: float | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Recommendation:
    """What the app tells the user, with everything needed to interrogate it."""

    week: int
    lineup: LineupPlan
    decisions: tuple[SlotDecision, ...]
    sources: tuple[SourceStatus, ...] = ()
    caveats: tuple[str, ...] = field(default_factory=tuple)
