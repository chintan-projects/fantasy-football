"""The judgment store.

The rule under test throughout: we persist what Yahoo cannot tell us later, and nothing
Yahoo already knows. There is deliberately no faab_balance column and no roster table --
mirroring those is how a balance silently goes stale after a bid placed from the Yahoo app.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ff.adapters.base import Approval
from ff.adapters.store import SCHEMA, Store
from ff.core.errors import ApprovalRequired


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "ff.db")


class TestWhatWeDoNotMirror:
    """CLAUDE.md's split: Yahoo owns state, we own judgment."""

    def test_there_is_no_faab_balance_column_anywhere(self) -> None:
        """Yahoo has faab_balance on the team resource and it is authoritative. A mirror
        drifts the first time a bid is placed from the Yahoo app, and a drifted balance is
        worse than none because it looks trustworthy."""
        assert "faab_balance" not in SCHEMA.lower()

    def test_there_is_no_roster_table(self) -> None:
        """Rosters come from roster;week=N, including past weeks. Nothing to mirror."""
        assert "create table if not exists rosters" not in SCHEMA.lower()

    def test_there_is_no_transactions_table(self) -> None:
        """Yahoo's transaction log is the source. We store only what we derive from it."""
        assert "create table if not exists transactions" not in SCHEMA.lower()


class TestSnapshots:
    def test_a_snapshot_round_trips(self, store: Store) -> None:
        payload = {"projections": {"p1": 12.4}, "sources": ["espn", "sleeper"]}
        store.save_snapshot(3, "lineup", payload)
        assert store.latest_snapshot(3, "lineup") == payload

    def test_the_latest_snapshot_wins(self, store: Store) -> None:
        """Re-running a recommendation mid-week must not lose the earlier inputs, but the
        newest is what the current recommendation was built on."""
        store.save_snapshot(3, "lineup", {"v": 1})
        time.sleep(0.01)
        store.save_snapshot(3, "lineup", {"v": 2})
        assert store.latest_snapshot(3, "lineup") == {"v": 2}

    def test_an_absent_snapshot_is_none_not_an_error(self, store: Store) -> None:
        assert store.latest_snapshot(99, "lineup") is None

    def test_snapshots_are_keyed_by_kind(self, store: Store) -> None:
        store.save_snapshot(3, "lineup", {"a": 1})
        store.save_snapshot(3, "waivers", {"b": 2})
        assert store.latest_snapshot(3, "lineup") == {"a": 1}
        assert store.latest_snapshot(3, "waivers") == {"b": 2}


class TestRecommendations:
    def test_a_recommendation_keeps_its_reasoning(self, store: Store) -> None:
        """A recommendation without its reasoning is not interrogable, and an
        uninterrogable recommendation will not be trusted. CLAUDE.md 2.5."""
        rec_id = store.save_recommendation(
            3, "lineup", {"starters": ["p1"]}, "Chase Brown has the better floor."
        )
        stored = store.list_recommendations(3)
        assert len(stored) == 1
        assert stored[0].id == rec_id
        assert "better floor" in stored[0].reasoning

    def test_a_recommendation_links_to_its_snapshot(self, store: Store) -> None:
        snapshot_id = store.save_snapshot(3, "lineup", {"proj": 1})
        store.save_recommendation(3, "lineup", {}, "because", snapshot_id=snapshot_id)
        assert store.list_recommendations(3)[0].week == 3

    def test_whether_it_was_followed_starts_unknown(self, store: Store) -> None:
        """Unknown is not the same as ignored, and the calibration math needs the
        difference."""
        rec_id = store.save_recommendation(3, "lineup", {}, "because")
        assert store.list_recommendations()[0].followed is None
        store.mark_followed(rec_id, True)
        assert store.list_recommendations()[0].followed is True

    def test_recommendations_come_back_newest_first(self, store: Store) -> None:
        store.save_recommendation(1, "lineup", {}, "one")
        time.sleep(0.01)
        store.save_recommendation(2, "lineup", {}, "two")
        assert [r.reasoning for r in store.list_recommendations()] == ["two", "one"]


class TestOutcomes:
    def test_an_outcome_round_trips(self, store: Store) -> None:
        store.record_outcome(3, "p1", 17.4)
        assert store.outcomes_for_week(3) == {"p1": 17.4}

    def test_a_corrected_stat_overwrites(self, store: Store) -> None:
        """Stat corrections land on Tuesday. The later number is the true one."""
        store.record_outcome(3, "p1", 17.4)
        store.record_outcome(3, "p1", 19.4)
        assert store.outcomes_for_week(3) == {"p1": 19.4}


class TestPreferences:
    def test_a_preference_round_trips(self, store: Store) -> None:
        pref = store.add_preference("Never drop Puka Nacua", kind="do_not_drop")
        assert [p.text for p in store.list_preferences()] == ["Never drop Puka Nacua"]
        assert pref.kind == "do_not_drop"

    def test_forgetting_deactivates_rather_than_deletes(self, store: Store) -> None:
        """A preference that was once true is history, and history is what calibration
        reads to explain a decision that looks wrong in hindsight."""
        pref = store.add_preference("Play it safe in the playoffs")
        assert store.forget_preference(pref.id) is True
        assert store.list_preferences(active_only=True) == []
        assert len(store.list_preferences(active_only=False)) == 1

    def test_forgetting_something_unknown_says_so(self, store: Store) -> None:
        assert store.forget_preference("nope") is False


class TestOpponentModel:
    def test_the_model_round_trips_per_team(self, store: Store) -> None:
        store.save_opponent_model("461.l.1", "461.l.1.t.2", {"aggression": 0.8})
        store.save_opponent_model("461.l.1", "461.l.1.t.3", {"aggression": 0.2})
        model = store.load_opponent_model("461.l.1")
        assert set(model) == {"461.l.1.t.2", "461.l.1.t.3"}
        assert model["461.l.1.t.2"]["aggression"] == 0.8

    def test_recomputing_replaces(self, store: Store) -> None:
        """It is a cache of a derivation, not a source of truth. Safe to recompute."""
        store.save_opponent_model("461.l.1", "461.l.1.t.2", {"aggression": 0.8})
        store.save_opponent_model("461.l.1", "461.l.1.t.2", {"aggression": 0.4})
        assert store.load_opponent_model("461.l.1")["461.l.1.t.2"]["aggression"] == 0.4

    def test_age_is_reported_so_staleness_can_be_shown(self, store: Store) -> None:
        assert store.opponent_model_age_seconds("461.l.1") is None
        store.save_opponent_model("461.l.1", "461.l.1.t.2", {})
        age = store.opponent_model_age_seconds("461.l.1")
        assert age is not None and age < 5


class TestApprovals:
    """Safety invariant 5.1, enforced by the database rather than by a set in memory."""

    def approval(self, week: int = 3) -> Approval:
        return Approval(
            approval_id="a1",
            payload_hash="deadbeef",
            week=week,
            created_at_epoch=time.time(),
            approved_by="owner",
        )

    def test_an_approval_can_be_spent_once(self, store: Store) -> None:
        store.issue_approval(self.approval())
        spent = store.consume_approval("a1")
        assert spent.payload_hash == "deadbeef"

    def test_spending_it_twice_is_refused(self, store: Store) -> None:
        """A replayable approval is a standing permission, which is exactly what
        CLAUDE.md 5.1 forbids."""
        store.issue_approval(self.approval())
        store.consume_approval("a1")
        with pytest.raises(ApprovalRequired, match="already used"):
            store.consume_approval("a1")

    def test_an_unknown_approval_is_refused(self, store: Store) -> None:
        with pytest.raises(ApprovalRequired, match="No such approval"):
            store.consume_approval("never-issued")

    def test_the_summary_survives_so_confirm_can_restate_the_plan(self, store: Store) -> None:
        """Confirming blind is the failure this two-step exists to prevent, so the second
        call has to be able to say what it is about to do."""
        store.issue_approval(self.approval(), summary="Bid $19 on Tyjae Spears")
        assert store.approval_summary("a1") == "Bid $19 on Tyjae Spears"

    def test_approvals_survive_a_new_store_object(self, tmp_path: Path) -> None:
        """Propose and confirm are two separate tool calls and may not share a process."""
        path = tmp_path / "ff.db"
        Store(path).issue_approval(self.approval())
        assert Store(path).consume_approval("a1").week == 3


class TestWriteJournal:
    def test_intent_is_written_before_the_result(self, store: Store) -> None:
        """The whole point: a crash between these two writes must be detectable."""
        jid = store.journal_intent(3, "submit_claim", "dryrun", {"bid": 19}, "a1")
        assert len(store.unfinished_writes()) == 1
        store.journal_result(jid, True, "HTTP 201")
        assert store.unfinished_writes() == []

    def test_an_unfinished_write_is_recoverable_by_reading_not_guessing(self, store: Store) -> None:
        store.journal_intent(3, "submit_claim", "yahoo", {"bid": 19, "player": "p1"}, "a1")
        pending = store.unfinished_writes()
        assert pending[0]["action"] == "submit_claim"
        assert "19" in pending[0]["request"]


class TestDurability:
    def test_the_schema_is_created_once_and_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "ff.db"
        Store(path).add_preference("first")
        assert len(Store(path).list_preferences()) == 1

    def test_write_ahead_logging_is_on(self, store: Store) -> None:
        """A Tuesday-night scheduled job writing must not block a tool call reading."""
        with store._conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
