"""The only place that reads the wall clock.

domain/ is pure, so time is always passed in. Everything else takes a Clock so tests can
freeze it rather than sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FrozenClock:
    at: datetime

    def now(self) -> datetime:
        return self.at
