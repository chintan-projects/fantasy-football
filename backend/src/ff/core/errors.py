"""One error family. Never invent a second way to signal failure."""

from __future__ import annotations


class FFError(Exception):
    """Base for everything this app raises."""


class ConfigError(FFError):
    """Missing or contradictory configuration. Fail at startup, not at 11:58 on Sunday."""


class SourceUnavailable(FFError):
    """An external source failed. Optional sources degrade; required sources stop the run."""

    def __init__(self, source: str, detail: str, *, required: bool) -> None:
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail
        self.required = required


class SchemaDrift(SourceUnavailable):
    """A source answered, but not in the shape we expect.

    Separate from a plain failure because it means an undocumented upstream changed, which
    needs a human, not a retry.
    """


class AuthExpired(FFError):
    """The Yahoo token could not be refreshed. A human has to re-authorize.

    Distinct from Throttled and from WriteRefused: no amount of retrying fixes it, and it
    is not a permissions problem -- the credential itself is gone.
    """


class Throttled(FFError):
    """Rate limited. Yahoo signals this with HTTP 999 and an HTML body."""

    def __init__(self, source: str, retry_after_s: float | None = None) -> None:
        super().__init__(f"{source} throttled")
        self.source = source
        self.retry_after_s = retry_after_s


class WriteRefused(FFError):
    """The provider refused a write. Usually a read-only token; see docs/YAHOO_SETUP.md."""


class ApprovalRequired(FFError):
    """A write was attempted with no valid approval. This should be unreachable."""


class ApprovalExpired(FFError):
    """The approval is older than the window, or was made for a different week."""


class BudgetExceeded(FFError):
    """A FAAB bid exceeds the live remaining budget. Reject; never silently clamp."""

    def __init__(self, bid: int, remaining: int) -> None:
        super().__init__(f"Bid ${bid} exceeds remaining budget ${remaining}")
        self.bid = bid
        self.remaining = remaining
