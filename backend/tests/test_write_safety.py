"""Safety invariants from CLAUDE.md section 5. These tests are the enforcement."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ff.adapters.base import Approval
from ff.adapters.store import Store
from ff.adapters.yahoo.executors import (
    AssistedExecutor,
    DryRunExecutor,
    check_approval,
    check_budget,
    payload_hash,
)
from ff.adapters.yahoo.payloads import add_drop_xml, edit_claim_xml, set_lineup_xml
from ff.core.errors import ApprovalExpired, ApprovalRequired, BudgetExceeded
from ff.domain.models import LineupPlan, PlayerId, Slot
from ff.services.approvals import ApprovalStore

PLAN = LineupPlan(
    week=3,
    assignments=((PlayerId("p1"), Slot.QB),),
    expected_points=100.0,
    win_probability=0.55,
    win_probability_band=(0.45, 0.65),
)


def approval_for(payload: object, week: int = 3, age_s: float = 0.0) -> Approval:
    return Approval(
        approval_id="a",
        payload_hash=payload_hash(payload),
        week=week,
        created_at_epoch=time.time() - age_s,
        approved_by="owner",
    )


def test_approval_for_a_different_payload_is_no_approval() -> None:
    stale = approval_for((("someone_else", "QB"),))
    with pytest.raises(ApprovalExpired):
        check_approval(stale, PLAN.assignments, PLAN.week)


def test_approval_for_a_different_week_is_rejected() -> None:
    with pytest.raises(ApprovalExpired):
        check_approval(approval_for(PLAN.assignments, week=2), PLAN.assignments, 3)


def test_approval_expires() -> None:
    with pytest.raises(ApprovalExpired):
        check_approval(approval_for(PLAN.assignments, age_s=7 * 3600), PLAN.assignments, 3)


def test_approval_is_single_use(tmp_path: Path) -> None:
    approvals = ApprovalStore(Store(tmp_path / "ff.db"))
    approval = approvals.issue(PLAN.assignments, week=3)
    approvals.consume(approval.approval_id)
    with pytest.raises(ApprovalRequired):
        approvals.consume(approval.approval_id)


def test_an_unknown_approval_is_not_an_approval(tmp_path: Path) -> None:
    approvals = ApprovalStore(Store(tmp_path / "ff.db"))
    with pytest.raises(ApprovalRequired):
        approvals.consume("made-up")


def test_over_budget_bid_is_rejected_not_clamped() -> None:
    with pytest.raises(BudgetExceeded):
        check_budget(40, 25)


def test_dryrun_is_the_default_and_sends_nothing() -> None:
    result = DryRunExecutor().set_lineup(PLAN, approval_for(PLAN.assignments))
    assert result.ok
    assert result.executor == "dryrun"
    assert result.transaction_key is None


def test_lineup_xml_puts_the_week_in_the_body() -> None:
    xml = set_lineup_xml(13, [("461.p.8332", "WR"), ("461.p.1423", "BN")])
    assert "<coverage_type>week</coverage_type>" in xml
    assert "<week>13</week>" in xml
    assert xml.index("461.p.8332") < xml.index("461.p.1423")


def test_faab_bid_is_a_sibling_of_type_and_precedes_players() -> None:
    """Two documented gotchas, locked down by test.

    faab_bid is a direct child of <transaction>, before <players> -- not inside
    transaction_data. And the drop leg takes source_team_key, despite Yahoo's own example
    using destination_team_key.
    """
    xml = add_drop_xml("461.l.1000.t.6", "461.p.5484", "461.p.6327", faab_bid=25)
    assert "<type>add/drop</type><faab_bid>25</faab_bid>" in xml
    assert xml.index("<faab_bid>") < xml.index("<players>")
    assert "<source_team_key>461.l.1000.t.6</source_team_key>" in xml
    assert xml.count("<destination_team_key>") == 1  # add leg only


def test_single_add_has_no_players_wrapper() -> None:
    xml = add_drop_xml("461.l.1000.t.6", "461.p.5484", None, faab_bid=12)
    assert "<players>" not in xml
    assert "<type>add</type><faab_bid>12</faab_bid>" in xml


def test_edit_claim_xml_shape() -> None:
    xml = edit_claim_xml("461.l.1000.w.c.2_6093", faab_bid=20, waiver_priority=1)
    assert "<type>waiver</type>" in xml
    assert "<transaction_key>461.l.1000.w.c.2_6093</transaction_key>" in xml
    assert "<faab_bid>20</faab_bid>" in xml


def test_payload_xml_escapes_hostile_input() -> None:
    xml = set_lineup_xml(1, [('461.p.1"><evil>', "QB")])
    assert "<evil>" not in xml


class TestAssistedDeepLink:
    """The assisted executor is the shipping path (CLAUDE.md section 6), and the deep link
    is the whole of what it delivers. A dead link is a dead product."""

    def test_a_bare_league_id_still_produces_a_usable_link(self) -> None:
        """The recommended configuration is the bare id. This used to emit /f1/ and
        nothing else, so the recommended setup produced the broken link."""
        assert AssistedExecutor("1000")._url("/team").endswith("/f1/1000/team")

    def test_a_full_league_key_drops_the_game_id(self) -> None:
        """The game id is an API concept. The web URL wants the league id alone."""
        assert AssistedExecutor("470.l.1000")._url("/team").endswith("/f1/1000/team")
