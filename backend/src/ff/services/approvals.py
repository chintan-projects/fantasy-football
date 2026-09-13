"""Approvals. The only thing that unlocks a write."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from ff.adapters.base import Approval
from ff.adapters.yahoo.executors import payload_hash
from ff.core.errors import ApprovalRequired


@dataclass
class ApprovalStore:
    """In-memory for now; one row per approval when this moves to SQLite.

    Single-use on purpose. An approval that can be replayed is a standing permission, which
    is exactly what CLAUDE.md 5.1 forbids.
    """

    _issued: dict[str, Approval] = field(default_factory=dict)
    _spent: set[str] = field(default_factory=set)

    def issue(self, payload: object, week: int, user: str = "owner") -> Approval:
        approval = Approval(
            approval_id=secrets.token_urlsafe(16),
            payload_hash=payload_hash(payload),
            week=week,
            created_at_epoch=time.time(),
            approved_by=user,
        )
        self._issued[approval.approval_id] = approval
        return approval

    def consume(self, approval_id: str) -> Approval:
        if approval_id in self._spent:
            raise ApprovalRequired("That approval was already used. Approve again.")
        approval = self._issued.get(approval_id)
        if approval is None:
            raise ApprovalRequired("No such approval.")
        self._spent.add(approval_id)
        return approval
