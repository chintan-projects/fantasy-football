"""Approvals. The only thing that unlocks a write.

Two tool calls, not one. ``ff_propose_claim`` computes a bid and issues an approval;
``ff_confirm`` spends it. That split is the whole safety story: a single tool that priced
and submitted in one hop would be a tool the model could invoke on its own initiative, and
CLAUDE.md 5.1 requires a recorded human yes against that exact payload.

The approval lives in SQLite rather than in a dict because the two calls are separate
requests and may not even be the same process. The payload lives with it, so confirm
submits the number the owner actually saw rather than re-deriving one that has since moved.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass

import httpx

from ff.adapters.base import Approval, WriteExecutor, WriteResult
from ff.adapters.store import Store
from ff.adapters.yahoo.auth import YahooAuth
from ff.adapters.yahoo.executors import (
    AssistedExecutor,
    DryRunExecutor,
    YahooApiExecutor,
    check_budget,
    payload_hash,
)
from ff.core.config import Settings
from ff.core.errors import ApprovalRequired, WriteRefused
from ff.core.logging import get_logger
from ff.domain.models import BidRecommendation, PlayerId

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ApprovalStore:
    """Issues and spends approvals. Single use, enforced by the database."""

    store: Store

    def issue(
        self,
        payload: object,
        week: int,
        user: str = "owner",
        summary: str = "",
    ) -> Approval:
        approval = Approval(
            approval_id=secrets.token_urlsafe(16),
            payload_hash=payload_hash(payload),
            week=week,
            created_at_epoch=time.time(),
            approved_by=user,
        )
        self.store.issue_approval(approval, summary=summary, payload=_as_dict(payload))
        log.info("approval_issued", approval_id=approval.approval_id, week=week)
        return approval

    def consume(self, approval_id: str) -> Approval:
        return self.store.consume_approval(approval_id)

    def approved_bid(self, approval_id: str) -> BidRecommendation:
        """Rebuild the bid exactly as it was proposed."""
        raw = self.store.approval_payload(approval_id)
        if not raw:
            raise ApprovalRequired("No such approval. Ask for the plan again.")
        return BidRecommendation(
            player_id=PlayerId(str(raw["player_id"])),
            reservation_value=int(raw["reservation_value"]),
            recommended_bid=int(raw["recommended_bid"]),
            shading_for_competition=float(raw["shading_for_competition"]),
            shading_for_winners_curse=float(raw["shading_for_winners_curse"]),
            option_value_penalty=float(raw["option_value_penalty"]),
            reason=str(raw["reason"]),
            drop_candidate=(
                PlayerId(str(raw["drop_candidate"])) if raw.get("drop_candidate") else None
            ),
        )


def _as_dict(payload: object) -> dict[str, object]:
    return asdict(payload) if hasattr(payload, "__dataclass_fields__") else {}  # type: ignore[call-overload]


def executor_for(config: Settings, auth: YahooAuth | None = None) -> WriteExecutor:
    """Pick the write executor named in configuration.

    ``dryrun`` is the default and sends nothing (CLAUDE.md 5.5). ``assisted`` is the path
    that keeps the product working if Yahoo never grants write access: it returns the exact
    move and a deep link, and the owner clicks. ``yahoo`` is the real API and is still
    unproven -- see CLAUDE.md section 6.
    """
    if config.write_executor == "assisted":
        return AssistedExecutor(config.yahoo_league_key)
    if config.write_executor == "yahoo":
        if auth is None:
            raise WriteRefused("The Yahoo executor needs an authenticated session.")
        return YahooApiExecutor(
            httpx.Client(headers=auth.headers(), timeout=30.0),
            team_key=config.yahoo_team_key,
            league_key=config.yahoo_league_key,
        )
    return DryRunExecutor()


def submit_claim(
    approvals: ApprovalStore,
    executor: WriteExecutor,
    approval_id: str,
    remaining_budget: int,
) -> WriteResult:
    """Spend one approval on one claim.

    Order matters and is the safety invariant in code: consume the approval first so a
    crash cannot leave it replayable, check the live budget second, journal the intent
    third, and only then send. ``remaining_budget`` must have been read from Yahoo moments
    ago -- a local tally would miss bids placed from the Yahoo app.
    """
    approval = approvals.consume(approval_id)
    bid = approvals.approved_bid(approval_id)
    check_budget(bid.recommended_bid, remaining_budget)

    request = {
        "player_id": str(bid.player_id),
        "bid": bid.recommended_bid,
        "week": approval.week,
    }
    journal_id = approvals.store.journal_intent(
        approval.week, "submit_claim", executor.name, request, approval.approval_id
    )
    try:
        result = executor.submit_claim(bid, approval)
    except Exception as exc:
        approvals.store.journal_result(journal_id, False, f"{type(exc).__name__}: {exc}")
        raise
    approvals.store.journal_result(journal_id, result.ok, result.detail)
    log.info(
        "claim_submitted",
        executor=executor.name,
        ok=result.ok,
        player=str(bid.player_id),
        bid=bid.recommended_bid,
    )
    return result
