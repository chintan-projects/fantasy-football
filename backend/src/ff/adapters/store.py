"""SQLite. The system of record for **judgment**.

The split that governs this whole module: **Yahoo owns state, we own judgment.**

Yahoo already knows the roster, the transactions, and -- critically -- the FAAB balance.
Never mirror any of it. A mirrored balance drifts the first time a bid is placed from the
Yahoo app, and a drifted balance is worse than no balance because it looks authoritative.
Read `faab_balance` off the team resource every time.

What Yahoo cannot tell us, and what is unrecoverable if not written down at decision time:

* the projections and the whole input set **as of the moment we decided** -- re-fetching
  next week gives different numbers, so a backtest without this is fiction
* the reasoning: which lineup won, by how much, and what the alternatives were
* what was recommended versus what the owner actually set -- the trust signal
* preferences that change the math (do-not-drop, risk posture)
* the derived opponent model

Without ``snapshots`` and ``outcomes`` the app can never answer "was it right?", which
CLAUDE.md 2.5 calls the single most important observability requirement in the project.

One connection per call, ``check_same_thread=False`` not needed: SQLite handles the
concurrency this app has (one user, a handful of requests a week). WAL is on so a
scheduled job writing does not block a tool call reading.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ff.adapters.base import Approval
from ff.core.errors import ApprovalRequired
from ff.core.logging import get_logger

log = get_logger(__name__)

SCHEMA_VERSION = 1

#: Created on first connect. No migration framework until there is a second version to
#: migrate to -- CLAUDE.md 2.0. ``schema_version`` exists so that day is not archaeology.
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- Every input set, exactly as it stood when a decision was made. A backtest replays
-- these; it never re-fetches, because the sources will have changed their minds.
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    week        INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    taken_at    REAL NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_week ON snapshots(week, kind);

-- What we told the owner, why, and -- once known -- what they actually did.
CREATE TABLE IF NOT EXISTS recommendations (
    id           TEXT PRIMARY KEY,
    week         INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    snapshot_id  INTEGER REFERENCES snapshots(id),
    created_at   REAL NOT NULL,
    payload      TEXT NOT NULL,
    reasoning    TEXT NOT NULL,
    followed     INTEGER,
    followed_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_recommendations_week ON recommendations(week, kind);

-- Actual points scored. Joined to snapshots, this answers "was it right?".
CREATE TABLE IF NOT EXISTS outcomes (
    week          INTEGER NOT NULL,
    player_id     TEXT NOT NULL,
    actual_points REAL NOT NULL,
    recorded_at   REAL NOT NULL,
    PRIMARY KEY (week, player_id)
);

-- Things the owner told us that change the math. Yahoo has no idea about any of these.
CREATE TABLE IF NOT EXISTS preferences (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at REAL NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

-- Derived per-manager bidding behaviour. Rebuilt from Yahoo's transaction log, so it is
-- a cache of a derivation rather than a source of truth -- safe to delete and recompute.
CREATE TABLE IF NOT EXISTS opponent_model (
    league_key  TEXT NOT NULL,
    team_key    TEXT NOT NULL,
    computed_at REAL NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (league_key, team_key)
);

-- Proof a human said yes to one exact payload. Single use, time limited.
-- In SQLite rather than memory because propose and confirm are two separate tool calls
-- and the process may not be the same one between them.
CREATE TABLE IF NOT EXISTS approvals (
    approval_id  TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    week         INTEGER NOT NULL,
    created_at   REAL NOT NULL,
    approved_by  TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    spent_at     REAL
);

-- intent -> approval -> request -> response. Crash recovery reads this rather than
-- guessing. CLAUDE.md 2.4.
CREATE TABLE IF NOT EXISTS write_journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    week        INTEGER NOT NULL,
    action      TEXT NOT NULL,
    approval_id TEXT,
    executor    TEXT NOT NULL,
    request     TEXT NOT NULL,
    response    TEXT,
    ok          INTEGER
);
"""


@dataclass(frozen=True, slots=True)
class Preference:
    id: str
    kind: str
    text: str
    created_at: float
    active: bool


@dataclass(frozen=True, slots=True)
class StoredRecommendation:
    id: str
    week: int
    kind: str
    created_at: float
    payload: dict[str, Any]
    reasoning: str
    followed: bool | None


class Store:
    """Everything this app knows that Yahoo does not."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            # WAL so a Tuesday-night scheduled job writing does not block a tool call
            # reading. Set once; it persists on the database file.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    # ---- snapshots ---------------------------------------------------------------

    def save_snapshot(self, week: int, kind: str, payload: dict[str, Any]) -> int:
        """Record the inputs as they stood. Returns the id to attach to a recommendation."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (week, kind, taken_at, payload) VALUES (?, ?, ?, ?)",
                (week, kind, time.time(), json.dumps(payload, default=str)),
            )
            snapshot_id = int(cur.lastrowid or 0)
        log.info("snapshot_saved", week=week, kind=kind, snapshot_id=snapshot_id)
        return snapshot_id

    def latest_snapshot(self, week: int, kind: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM snapshots WHERE week = ? AND kind = ? "
                "ORDER BY taken_at DESC LIMIT 1",
                (week, kind),
            ).fetchone()
        return None if row is None else dict(json.loads(row["payload"]))

    # ---- recommendations ---------------------------------------------------------

    def save_recommendation(
        self,
        week: int,
        kind: str,
        payload: dict[str, Any],
        reasoning: str,
        snapshot_id: int | None = None,
    ) -> str:
        rec_id = secrets.token_urlsafe(12)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO recommendations "
                "(id, week, kind, snapshot_id, created_at, payload, reasoning) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rec_id,
                    week,
                    kind,
                    snapshot_id,
                    time.time(),
                    json.dumps(payload, default=str),
                    reasoning,
                ),
            )
        return rec_id

    def mark_followed(self, rec_id: str, followed: bool) -> None:
        """The trust signal: did the owner actually do what we said?"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE recommendations SET followed = ?, followed_at = ? WHERE id = ?",
                (1 if followed else 0, time.time(), rec_id),
            )

    def list_recommendations(
        self, week: int | None = None, limit: int = 20
    ) -> list[StoredRecommendation]:
        query = "SELECT * FROM recommendations"
        params: tuple[Any, ...] = ()
        if week is not None:
            query += " WHERE week = ?"
            params = (week,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params += (limit,)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            StoredRecommendation(
                id=r["id"],
                week=r["week"],
                kind=r["kind"],
                created_at=r["created_at"],
                payload=json.loads(r["payload"]),
                reasoning=r["reasoning"],
                followed=None if r["followed"] is None else bool(r["followed"]),
            )
            for r in rows
        ]

    # ---- outcomes ----------------------------------------------------------------

    def record_outcome(self, week: int, player_id: str, actual_points: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO outcomes (week, player_id, actual_points, recorded_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(week, player_id) DO UPDATE SET "
                "actual_points = excluded.actual_points, recorded_at = excluded.recorded_at",
                (week, player_id, actual_points, time.time()),
            )

    def outcomes_for_week(self, week: int) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT player_id, actual_points FROM outcomes WHERE week = ?", (week,)
            ).fetchall()
        return {r["player_id"]: r["actual_points"] for r in rows}

    # ---- preferences -------------------------------------------------------------

    def add_preference(self, text: str, kind: str = "note") -> Preference:
        pref = Preference(
            id=secrets.token_urlsafe(8),
            kind=kind,
            text=text.strip(),
            created_at=time.time(),
            active=True,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO preferences (id, kind, text, created_at, active) "
                "VALUES (?, ?, ?, ?, 1)",
                (pref.id, pref.kind, pref.text, pref.created_at),
            )
        log.info("preference_recorded", kind=kind)
        return pref

    def list_preferences(self, active_only: bool = True) -> list[Preference]:
        query = "SELECT * FROM preferences"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query).fetchall()
        return [
            Preference(
                id=r["id"],
                kind=r["kind"],
                text=r["text"],
                created_at=r["created_at"],
                active=bool(r["active"]),
            )
            for r in rows
        ]

    def forget_preference(self, pref_id: str) -> bool:
        """Deactivate rather than delete. A preference that was once true is history."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE preferences SET active = 0 WHERE id = ?", (pref_id,))
            return cur.rowcount > 0

    # ---- opponent model ----------------------------------------------------------

    def save_opponent_model(self, league_key: str, team_key: str, payload: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO opponent_model (league_key, team_key, computed_at, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(league_key, team_key) DO UPDATE SET "
                "computed_at = excluded.computed_at, payload = excluded.payload",
                (league_key, team_key, time.time(), json.dumps(payload, default=str)),
            )

    def load_opponent_model(self, league_key: str) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT team_key, payload FROM opponent_model WHERE league_key = ?",
                (league_key,),
            ).fetchall()
        return {r["team_key"]: json.loads(r["payload"]) for r in rows}

    def opponent_model_age_seconds(self, league_key: str) -> float | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(computed_at) AS at FROM opponent_model WHERE league_key = ?",
                (league_key,),
            ).fetchone()
        if row is None or row["at"] is None:
            return None
        return time.time() - float(row["at"])

    # ---- approvals ---------------------------------------------------------------

    def issue_approval(self, approval: Approval, summary: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO approvals "
                "(approval_id, payload_hash, week, created_at, approved_by, summary) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    approval.approval_id,
                    approval.payload_hash,
                    approval.week,
                    approval.created_at_epoch,
                    approval.approved_by,
                    summary,
                ),
            )

    def consume_approval(self, approval_id: str) -> Approval:
        """Single use, enforced by the database rather than by a set in memory.

        The UPDATE ... WHERE spent_at IS NULL is the whole mechanism: two concurrent
        confirmations race on one row and exactly one wins.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE approvals SET spent_at = ? WHERE approval_id = ? AND spent_at IS NULL",
                (time.time(), approval_id),
            )
            if cur.rowcount == 0:
                exists = conn.execute(
                    "SELECT 1 FROM approvals WHERE approval_id = ?", (approval_id,)
                ).fetchone()
                if exists is None:
                    raise ApprovalRequired("No such approval. Ask for the plan again.")
                raise ApprovalRequired("That approval was already used. Ask for a fresh plan.")
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return Approval(
            approval_id=row["approval_id"],
            payload_hash=row["payload_hash"],
            week=row["week"],
            created_at_epoch=row["created_at"],
            approved_by=row["approved_by"],
        )

    def approval_summary(self, approval_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT summary FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return None if row is None else str(row["summary"])

    # ---- write journal -----------------------------------------------------------

    def journal_intent(
        self, week: int, action: str, executor: str, request: dict[str, Any], approval_id: str
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO write_journal (at, week, action, approval_id, executor, request) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    week,
                    action,
                    approval_id,
                    executor,
                    json.dumps(request, default=str),
                ),
            )
            return int(cur.lastrowid or 0)

    def journal_result(self, journal_id: int, ok: bool, response: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE write_journal SET ok = ?, response = ? WHERE id = ?",
                (1 if ok else 0, response, journal_id),
            )

    def unfinished_writes(self) -> list[dict[str, Any]]:
        """Rows with an intent and no response. A crash mid-write leaves exactly these."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM write_journal WHERE ok IS NULL").fetchall()
        return [dict(r) for r in rows]
