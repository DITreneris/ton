"""Durable paper ledger. Restart recovery reconciles unresolved intents first."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from ton_gram.clock import Clock, format_utc, parse_utc
from ton_gram.config import Settings
from ton_gram.fills import apply_fill_to_balances, simulate_fill
from ton_gram.models import (
    AssessmentRecordStatus,
    Candidate,
    Direction,
    HaltReason,
    Intent,
    IntentStatus,
    PaperFill,
    Position,
    Quote,
    RiskDecision,
    RiskVerdict,
    to_plain,
)
from ton_gram.units import mark_usdt_micro

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS control_state (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    offer_units INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_decisions (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    verdict TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    wallet_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    direction TEXT NOT NULL,
    offer_units INTEGER NOT NULL,
    reserved_usdt_micro INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    terminal_at_utc TEXT,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    filled_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    wallet_id TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    gram_nano INTEGER NOT NULL,
    usdt_micro INTEGER NOT NULL,
    reserved_usdt_micro INTEGER NOT NULL,
    equity_usdt_micro INTEGER NOT NULL,
    peak_equity_usdt_micro INTEGER NOT NULL,
    daily_pnl_usdt_micro INTEGER NOT NULL,
    realized_pnl_usdt_micro INTEGER NOT NULL,
    day_realized_pnl_usdt_micro INTEGER NOT NULL DEFAULT 0,
    peak_clip_equity_usdt_micro INTEGER NOT NULL DEFAULT 0,
    day_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    as_of_utc TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at_utc TEXT NOT NULL,
    snapshot_id TEXT,
    record_status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_key TEXT NOT NULL,
    sent_at_utc TEXT NOT NULL,
    PRIMARY KEY (alert_key, sent_at_utc)
);
CREATE TABLE IF NOT EXISTS daily_reports (
    day_utc TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_utc TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    quote_age_ms INTEGER
);
CREATE TABLE IF NOT EXISTS halt_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of_utc TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


class Ledger:
    def __init__(self, settings: Settings, clock: Clock, db_path: Path | None = None) -> None:
        self.settings = settings
        self.clock = clock
        path = db_path or settings.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_control()
        self._migrate_position_columns()
        self._ensure_position()

    def close(self) -> None:
        self.conn.close()

    def _ensure_control(self) -> None:
        defaults = {
            "entry_halt": "0",
            "halt_reason": "",
            "recovered": "0",
            "seq": "0",
            "last_heartbeat_utc": "",
            "gas_profile_json": "",
            "strategy_state_json": "",
        }
        for key, value in defaults.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO control_state(k, v) VALUES (?, ?)",
                (key, value),
            )
        self.conn.commit()

    def _migrate_position_columns(self) -> None:
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(positions)").fetchall()}
        if "day_realized_pnl_usdt_micro" not in cols:
            self.conn.execute(
                "ALTER TABLE positions ADD COLUMN day_realized_pnl_usdt_micro INTEGER NOT NULL DEFAULT 0"
            )
        if "peak_clip_equity_usdt_micro" not in cols:
            cap = int(self.settings.funding_cap_usdt_micro)
            self.conn.execute(
                "ALTER TABLE positions ADD COLUMN peak_clip_equity_usdt_micro "
                f"INTEGER NOT NULL DEFAULT {cap}"
            )
        self.conn.commit()
        row = self.conn.execute("SELECT peak_clip_equity_usdt_micro FROM positions WHERE id = 1").fetchone()
        if row is not None and int(row["peak_clip_equity_usdt_micro"] or 0) <= 0:
            self.conn.execute(
                "UPDATE positions SET peak_clip_equity_usdt_micro = ? WHERE id = 1",
                (int(self.settings.funding_cap_usdt_micro),),
            )
            self.conn.commit()

    def _ensure_position(self) -> None:
        row = self.conn.execute("SELECT 1 FROM positions WHERE id = 1").fetchone()
        if row:
            return
        now = format_utc(self.clock.now())
        cap = int(self.settings.funding_cap_usdt_micro)
        self.conn.execute(
            """
            INSERT INTO positions(
                id, wallet_id, as_of_utc, gram_nano, usdt_micro, reserved_usdt_micro,
                equity_usdt_micro, peak_equity_usdt_micro, daily_pnl_usdt_micro,
                realized_pnl_usdt_micro, day_realized_pnl_usdt_micro,
                peak_clip_equity_usdt_micro, day_utc
            ) VALUES (1, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, ?, '')
            """,
            (
                self.settings.paper_wallet_id,
                now,
                self.settings.paper_gram_nano,
                self.settings.paper_usdt_micro,
                cap,
            ),
        )
        self.conn.commit()

    def next_id(self, prefix: str) -> str:
        row = self.conn.execute("SELECT v FROM control_state WHERE k = 'seq'").fetchone()
        seq = int(row["v"]) + 1 if row else 1
        self.conn.execute("UPDATE control_state SET v = ? WHERE k = 'seq'", (str(seq),))
        self.conn.commit()
        stamp = format_utc(self.clock.now()).replace(":", "").replace("-", "")
        return f"{prefix}_{stamp}_{seq}"

    def get_control(self, key: str) -> str:
        row = self.conn.execute("SELECT v FROM control_state WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else ""

    def set_control(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO control_state(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )
        self.conn.commit()

    @property
    def entry_halt(self) -> bool:
        return self.get_control("entry_halt") == "1"

    @property
    def halt_reason(self) -> str:
        return self.get_control("halt_reason")

    def latch_halt(self, reason: HaltReason | str) -> bool:
        """Sticky halt. First reason wins until rearm.

        Later feed 500s and limit re-checks are recorded as ``latch_ignored``
        and must not rewrite ``halt_reason``. ``SETTLEMENT_UNKNOWN`` is the
        only upgrade: a missing quote is worse than a limit card.

        Returns True when this call set or upgraded the visible reason.
        """
        reason_s = str(reason)
        already = self.entry_halt
        prior = self.halt_reason
        upgrade = reason_s == HaltReason.SETTLEMENT_UNKNOWN.value and prior != reason_s
        self.set_control("entry_halt", "1")
        if not already or upgrade:
            self.set_control("halt_reason", reason_s)
            action = "latch_upgrade" if already and upgrade else "latch"
            changed = True
        else:
            action = "latch_ignored"
            changed = False
        self.conn.execute(
            "INSERT INTO halt_events(as_of_utc, action, reason) VALUES (?, ?, ?)",
            (format_utc(self.clock.now()), action, reason_s),
        )
        self.conn.commit()
        return changed

    def rearm(self) -> None:
        self.set_control("entry_halt", "0")
        self.set_control("halt_reason", "")
        self.conn.execute(
            "INSERT INTO halt_events(as_of_utc, action, reason) VALUES (?, ?, ?)",
            (format_utc(self.clock.now()), "rearm", ""),
        )
        self.conn.commit()

    def ingest_event(self, event_id: str, kind: str, payload: dict[str, Any]) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO events(event_id, kind, created_at_utc, payload_json) VALUES (?, ?, ?, ?)",
                (event_id, kind, format_utc(self.clock.now()), _json(payload)),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def save_quote(self, quote: Quote) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO quotes(quote_id, direction, observed_at_utc, payload_json) VALUES (?, ?, ?, ?)",
            (quote.quote_id, quote.direction.value, quote.observed_at_utc, _json(to_plain(quote))),
        )
        self.conn.commit()

    def get_quote(self, quote_id: str) -> Quote | None:
        row = self.conn.execute("SELECT payload_json FROM quotes WHERE quote_id = ?", (quote_id,)).fetchone()
        if not row:
            return None
        return _quote_from_payload(json.loads(row["payload_json"]))

    def latest_quotes(self) -> dict[Direction, Quote]:
        out: dict[Direction, Quote] = {}
        for direction in Direction:
            row = self.conn.execute(
                "SELECT payload_json FROM quotes WHERE direction = ? ORDER BY observed_at_utc DESC LIMIT 1",
                (direction.value,),
            ).fetchone()
            if row:
                out[direction] = _quote_from_payload(json.loads(row["payload_json"]))
        return out

    def save_candidate(self, candidate: Candidate) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO candidates(
                candidate_id, created_at_utc, quote_id, direction, offer_units, policy_version, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.created_at_utc,
                candidate.quote_id,
                candidate.direction.value,
                candidate.offer_units,
                candidate.policy_version,
                _json(to_plain(candidate)),
            ),
        )
        self.conn.commit()

    def save_decision(self, decision: RiskDecision) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO risk_decisions(
                decision_id, candidate_id, quote_id, policy_version, verdict, created_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.candidate_id,
                decision.quote_id,
                decision.policy_version,
                decision.verdict.value,
                decision.created_at_utc,
                _json(to_plain(decision)),
            ),
        )
        self.conn.commit()

    def get_position(self) -> Position:
        row = self.conn.execute("SELECT * FROM positions WHERE id = 1").fetchone()
        return Position(
            wallet_id=row["wallet_id"],
            as_of_utc=row["as_of_utc"],
            gram_nano=row["gram_nano"],
            usdt_micro=row["usdt_micro"],
            reserved_usdt_micro=row["reserved_usdt_micro"],
            equity_usdt_micro=row["equity_usdt_micro"],
            peak_equity_usdt_micro=row["peak_equity_usdt_micro"],
            daily_pnl_usdt_micro=row["daily_pnl_usdt_micro"],
            realized_pnl_usdt_micro=row["realized_pnl_usdt_micro"],
            day_realized_pnl_usdt_micro=int(row["day_realized_pnl_usdt_micro"] or 0),
            peak_clip_equity_usdt_micro=int(
                row["peak_clip_equity_usdt_micro"] or self.settings.funding_cap_usdt_micro
            ),
            day_utc=row["day_utc"],
        )

    def _write_position(self, position: Position) -> None:
        self.conn.execute(
            """
            UPDATE positions SET
                wallet_id=?, as_of_utc=?, gram_nano=?, usdt_micro=?, reserved_usdt_micro=?,
                equity_usdt_micro=?, peak_equity_usdt_micro=?, daily_pnl_usdt_micro=?,
                realized_pnl_usdt_micro=?, day_realized_pnl_usdt_micro=?,
                peak_clip_equity_usdt_micro=?, day_utc=?
            WHERE id = 1
            """,
            (
                position.wallet_id,
                position.as_of_utc,
                position.gram_nano,
                position.usdt_micro,
                position.reserved_usdt_micro,
                position.equity_usdt_micro,
                position.peak_equity_usdt_micro,
                position.daily_pnl_usdt_micro,
                position.realized_pnl_usdt_micro,
                position.day_realized_pnl_usdt_micro,
                position.peak_clip_equity_usdt_micro,
                position.day_utc,
            ),
        )
        self.conn.commit()
