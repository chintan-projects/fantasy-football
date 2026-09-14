"""The three write executors. Every one takes an Approval as a required argument."""

from __future__ import annotations

import hashlib
import json
import time

from ff.adapters.base import Approval, WriteResult, write_executors
from ff.adapters.yahoo.payloads import add_drop_xml, set_lineup_xml
from ff.core.config import settings
from ff.core.errors import ApprovalExpired, BudgetExceeded, WriteRefused
from ff.core.logging import get_logger
from ff.domain.models import BidRecommendation, LineupPlan, TransactionKey

log = get_logger(__name__)


def payload_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def check_approval(
    approval: Approval, payload: object, week: int, now: float | None = None
) -> None:
    """An approval covers one payload, one week, for six hours. Nothing else.

    Re-checked immediately before every write. An approval for a different payload is not a
    weaker approval -- it is no approval.
    """
    now = time.time() if now is None else now
    if approval.week != week:
        raise ApprovalExpired(f"Approval is for week {approval.week}, not week {week}.")
    if now - approval.created_at_epoch > settings().approval_ttl_seconds:
        raise ApprovalExpired("Approval is older than the window. Approve again.")
    if approval.payload_hash != payload_hash(payload):
        raise ApprovalExpired("Approval does not match this payload. Approve the current plan.")


def check_budget(bid: int, remaining: int) -> None:
    """Reject, never clamp. A clamped bid is a bid the user did not approve."""
    if bid > remaining:
        raise BudgetExceeded(bid, remaining)


@write_executors.register("dryrun")
class DryRunExecutor:
    """The default. Journals what would have happened and sends nothing."""

    name = "dryrun"

    def set_lineup(self, plan: LineupPlan, approval: Approval) -> WriteResult:
        check_approval(approval, plan.assignments, plan.week)
        xml = set_lineup_xml(plan.week, [(str(p), str(s)) for p, s in plan.assignments])
        log.info("dryrun_set_lineup", week=plan.week, payload_bytes=len(xml))
        return WriteResult(True, self.name, f"Dry run. Would PUT {len(xml)} bytes of roster XML.")

    def submit_claim(self, bid: BidRecommendation, approval: Approval) -> WriteResult:
        check_approval(approval, bid, approval.week)
        log.info("dryrun_claim", player=bid.player_id, bid=bid.recommended_bid)
        return WriteResult(
            True, self.name, f"Dry run. Would bid ${bid.recommended_bid} on {bid.player_id}."
        )

    def cancel_claim(self, key: TransactionKey, approval: Approval) -> WriteResult:
        check_approval(approval, key, approval.week)
        return WriteResult(True, self.name, f"Dry run. Would cancel {key}.")


@write_executors.register("assisted")
class AssistedExecutor:
    """The fallback if Yahoo refuses write access.

    Does the analysis, hands back the exact move and a deep link. The product still works;
    only the clicking is manual.
    """

    name = "assisted"

    def __init__(self, league_key: str | None = None) -> None:
        self.league_key = league_key or settings().yahoo_league_key

    def _url(self, path: str = "") -> str:
        """The deep link is this executor's entire product, so it has to survive both
        spellings of the league key.

        It used to drop a bare league id on the floor and emit ``/f1/`` -- a dead link --
        and the bare id is the spelling docs/YAHOO_SETUP.md recommends. The web URL wants
        the league id alone either way; the game id prefix is an API concept.
        """
        league_id = self.league_key.split(".l.")[-1]
        return f"https://football.fantasysports.yahoo.com/f1/{league_id}{path}"

    def set_lineup(self, plan: LineupPlan, approval: Approval) -> WriteResult:
        check_approval(approval, plan.assignments, plan.week)
        moves = ", ".join(f"{pid} -> {slot}" for pid, slot in plan.assignments)
        return WriteResult(
            True, self.name, f"Set these by hand: {moves}", manual_url=self._url("/team")
        )

    def submit_claim(self, bid: BidRecommendation, approval: Approval) -> WriteResult:
        check_approval(approval, bid, approval.week)
        return WriteResult(
            True,
            self.name,
            f"Claim {bid.player_id} for ${bid.recommended_bid}.",
            manual_url=self._url("/players?status=W"),
        )

    def cancel_claim(self, key: TransactionKey, approval: Approval) -> WriteResult:
        check_approval(approval, key, approval.week)
        return WriteResult(True, self.name, f"Cancel {key} by hand.", manual_url=self._url())


@write_executors.register("yahoo")
class YahooApiExecutor:
    """The real thing, if write access is granted.

    Unproven until scripts/probe_write.py succeeds -- Yahoo states the API is read-only by
    default and grants writes only by review. See CLAUDE.md section 6.
    """

    name = "yahoo"

    def __init__(self, session: object, team_key: str, league_key: str) -> None:
        self.session = session
        self.team_key = team_key
        self.league_key = league_key
        self.base = "https://fantasysports.yahooapis.com/fantasy/v2"
        self.headers = {"Content-Type": "application/xml"}

    def _send(self, method: str, path: str, body: str | None = None) -> WriteResult:
        if not settings().write_enabled:
            raise WriteRefused("FF_WRITE_ENABLED is false. Writes are off by default.")
        resp = getattr(self.session, method)(  # type: ignore[attr-defined]
            f"{self.base}{path}", data=body, headers=self.headers
        )
        # Status before parsing, always: a throttled Yahoo returns HTTP 999 with HTML.
        status = getattr(resp, "status_code", 0)
        if status in (401, 403):
            raise WriteRefused(
                f"HTTP {status}. Most likely a read-only token: check that Read/Write is "
                f"enabled on the app in YDN and that the authorize URL requested fspt-w. "
                f"See docs/YAHOO_SETUP.md."
            )
        if status not in (200, 201):
            return WriteResult(False, self.name, f"HTTP {status}")
        return WriteResult(True, self.name, f"HTTP {status}")

    def set_lineup(self, plan: LineupPlan, approval: Approval) -> WriteResult:
        check_approval(approval, plan.assignments, plan.week)
        xml = set_lineup_xml(plan.week, [(str(p), str(s)) for p, s in plan.assignments])
        return self._send("put", f"/team/{self.team_key}/roster", xml)

    def submit_claim(
        self, bid: BidRecommendation, approval: Approval, remaining_budget: int | None = None
    ) -> WriteResult:
        check_approval(approval, bid, approval.week)
        if remaining_budget is not None:
            check_budget(bid.recommended_bid, remaining_budget)
        xml = add_drop_xml(
            self.team_key,
            str(bid.player_id),
            str(bid.drop_candidate) if bid.drop_candidate else None,
            bid.recommended_bid,
        )
        return self._send("post", f"/league/{self.league_key}/transactions", xml)

    def cancel_claim(self, key: TransactionKey, approval: Approval) -> WriteResult:
        check_approval(approval, key, approval.week)
        return self._send("delete", f"/transaction/{key}")
