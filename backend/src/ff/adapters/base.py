"""Adapter protocols and the provider registry.

These are the three seams that exist up front, because each has a known second
implementation (CLAUDE.md 2.3). Nothing else gets an interface until a second caller is
real.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from ff.core.errors import ConfigError
from ff.domain.models import (
    BidRecommendation,
    LineupPlan,
    Player,
    PlayerId,
    Projection,
    SourceStatus,
    TransactionKey,
)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Approval:
    """Proof that a human said yes to this exact payload.

    Every write takes one of these as a required argument, so there is no code path -- now
    or later, written in a hurry -- that can skip the check. That is the enforcement
    mechanism for CLAUDE.md safety invariant 5.1: a type error, not a policy.
    """

    approval_id: str
    payload_hash: str
    week: int
    created_at_epoch: float
    approved_by: str


@dataclass(frozen=True, slots=True)
class WriteResult:
    ok: bool
    executor: str
    detail: str
    transaction_key: TransactionKey | None = None
    manual_url: str | None = None


class ProjectionSource(Protocol):
    """One forecaster. At least two must be configured -- ensembles win."""

    name: str
    required: bool

    def weekly(self, week: int, players: list[PlayerId]) -> dict[PlayerId, Projection]: ...
    def status(self) -> SourceStatus: ...


class RosterSource(Protocol):
    """Reads the league. Yahoo today; nothing else planned, so this stays minimal."""

    def league_settings(self) -> object: ...
    def roster(self, week: int) -> object: ...
    def free_agents(self, position: str | None = None) -> list[Player]: ...


class WriteExecutor(Protocol):
    """Applies a decision. Three implementations; see docs/ARCHITECTURE.md.

    - ``DryRunExecutor``  journals intent, sends nothing. The default.
    - ``YahooApiExecutor`` the real API, if write access is granted.
    - ``AssistedExecutor`` renders the move plus a deep link; the human clicks.
    """

    name: str

    def set_lineup(self, plan: LineupPlan, approval: Approval) -> WriteResult: ...
    def submit_claim(self, bid: BidRecommendation, approval: Approval) -> WriteResult: ...
    def cancel_claim(self, key: TransactionKey, approval: Approval) -> WriteResult: ...


class Registry:
    """Adding a provider is a new file plus one registration -- never an edit to a switch
    statement scattered across the codebase."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: dict[str, Callable[[], object]] = {}

    def register(self, name: str) -> Callable[[Callable[[], T]], Callable[[], T]]:
        def deco(factory: Callable[[], T]) -> Callable[[], T]:
            self._factories[name] = factory
            return factory

        return deco

    def create(self, name: str) -> object:
        if name not in self._factories:
            known = ", ".join(sorted(self._factories)) or "(none)"
            raise ConfigError(f"Unknown {self.kind} '{name}'. Registered: {known}")
        return self._factories[name]()

    def names(self) -> list[str]:
        return sorted(self._factories)


projection_sources = Registry("projection source")
write_executors = Registry("write executor")
