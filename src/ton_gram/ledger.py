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
        }
        for key, value in defaults.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO control_state(k, v) VALUES (?, ?)",
                (key, value),
            )
        self.conn.commit()

    def _ensure_position(self) -> None:
        row = self.conn.execute("SELECT 1 FROM positions WHERE id = 1").fetchone()
        if row:
            return
        now = format_utc(self.clock.now())
        self.conn.execute(
            """
            INSERT INTO positions(
                id, wallet_id, as_of_utc, gram_nano, usdt_micro, reserved_usdt_micro,
                equity_usdt_micro, peak_equity_usdt_micro, daily_pnl_usdt_micro,
                realized_pnl_usdt_micro, day_utc
            ) VALUES (1, ?, ?, ?, ?, 0, 0, 0, 0, 0, '')
            """,
            (
                self.settings.paper_wallet_id,
                now,
                self.settings.paper_gram_nano,
                self.settings.paper_usdt_micro,
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

    def latch_halt(self, reason: HaltReason | str) -> None:
        self.set_control("entry_halt", "1")
        self.set_control("halt_reason", str(reason))
        self.conn.execute(
            "INSERT INTO halt_events(as_of_utc, action, reason) VALUES (?, ?, ?)",
            (format_utc(self.clock.now()), "latch", str(reason)),
        )
        self.conn.commit()

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
            day_utc=row["day_utc"],
        )

    def _write_position(self, position: Position) -> None:
        self.conn.execute(
            """
            UPDATE positions SET
                wallet_id=?, as_of_utc=?, gram_nano=?, usdt_micro=?, reserved_usdt_micro=?,
                equity_usdt_micro=?, peak_equity_usdt_micro=?, daily_pnl_usdt_micro=?,
                realized_pnl_usdt_micro=?, day_utc=?
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
                position.day_utc,
            ),
        )
        self.conn.commit()

    def mark_to_market(self, mid_usdt_per_gram_micro: int) -> Position:
        position = self.get_position()
        now = format_utc(self.clock.now())
        day = now[:10]
        equity = mark_usdt_micro(position.gram_nano, position.usdt_micro, mid_usdt_per_gram_micro)
        if day != position.day_utc:
            position.daily_pnl_usdt_micro = 0
            position.day_utc = day
            sod_equity = equity
        else:
            sod_equity = equity - position.daily_pnl_usdt_micro
            start = position.equity_usdt_micro - position.daily_pnl_usdt_micro
            sod_equity = start
        position.as_of_utc = now
        position.equity_usdt_micro = equity
        position.daily_pnl_usdt_micro = equity - sod_equity
        position.peak_equity_usdt_micro = max(position.peak_equity_usdt_micro, equity)
        self._write_position(position)
        return position

    def unresolved_intents(self, wallet_id: str | None = None) -> list[Intent]:
        wallet = wallet_id or self.settings.paper_wallet_id
        rows = self.conn.execute(
            "SELECT * FROM intents WHERE wallet_id = ? AND status = ? ORDER BY created_at_utc",
            (wallet, IntentStatus.RESERVED.value),
        ).fetchall()
        return [_intent_from_row(row) for row in rows]

    def reserve_intent(self, intent: Intent) -> None:
        if self.unresolved_intents(intent.wallet_id):
            raise RuntimeError("one unresolved intent per wallet")
        self.conn.execute(
            """
            INSERT INTO intents(
                intent_id, wallet_id, candidate_id, quote_id, decision_id, policy_version,
                direction, offer_units, reserved_usdt_micro, status, created_at_utc,
                expires_at_utc, terminal_at_utc, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.intent_id,
                intent.wallet_id,
                intent.candidate_id,
                intent.quote_id,
                intent.decision_id,
                intent.policy_version,
                intent.direction.value,
                intent.offer_units,
                intent.reserved_usdt_micro,
                intent.status.value,
                intent.created_at_utc,
                intent.expires_at_utc,
                intent.terminal_at_utc,
                intent.notes,
            ),
        )
        position = self.get_position()
        position.reserved_usdt_micro += intent.reserved_usdt_micro
        self._write_position(position)

    def _close_intent(self, intent_id: str, status: IntentStatus, notes: str) -> None:
        now = format_utc(self.clock.now())
        row = self.conn.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
        if row is None:
            return
        intent = _intent_from_row(row)
        if intent.status is not IntentStatus.RESERVED:
            return
        self.conn.execute(
            "UPDATE intents SET status = ?, terminal_at_utc = ?, notes = ? WHERE intent_id = ?",
            (status.value, now, notes, intent_id),
        )
        position = self.get_position()
        position.reserved_usdt_micro = max(0, position.reserved_usdt_micro - intent.reserved_usdt_micro)
        self._write_position(position)

    def save_fill(self, fill: PaperFill, direction: Direction) -> Position:
        self.conn.execute(
            "INSERT OR REPLACE INTO fills(fill_id, intent_id, quote_id, filled_at_utc, payload_json) VALUES (?, ?, ?, ?, ?)",
            (fill.fill_id, fill.intent_id, fill.quote_id, fill.filled_at_utc, _json(to_plain(fill))),
        )
        intent_row = self.conn.execute("SELECT * FROM intents WHERE intent_id = ?", (fill.intent_id,)).fetchone()
        intent = _intent_from_row(intent_row)
        position = self.get_position()
        gram, usdt = apply_fill_to_balances(
            gram_nano=position.gram_nano,
            usdt_micro=position.usdt_micro,
            direction=direction,
            offer_units=intent.offer_units,
            fill=fill,
            gas_reserve_nano=self.settings.gram_gas_reserve_nano,
        )
        realized = position.realized_pnl_usdt_micro - fill.network_cost_usdt_micro
        if direction is Direction.GRAM_TO_USDT:
            realized += fill.filled_ask_units - (
                (intent.offer_units * fill.mid_usdt_per_gram_micro) // 1_000_000_000
            )
        else:
            received = (fill.filled_ask_units * fill.mid_usdt_per_gram_micro) // 1_000_000_000
            realized += received - intent.offer_units
        position.gram_nano = gram
        position.usdt_micro = usdt
        position.realized_pnl_usdt_micro = realized
        self._write_position(position)
        self._close_intent(fill.intent_id, IntentStatus.FILLED_PAPER, "paper_fill")
        return self.mark_to_market(fill.mid_usdt_per_gram_micro)

    def expire_intent(self, intent_id: str, notes: str) -> None:
        self._close_intent(intent_id, IntentStatus.EXPIRED, notes)

    def reject_intent(self, intent_id: str, notes: str) -> None:
        self._close_intent(intent_id, IntentStatus.REJECTED, notes)

    def recover(self) -> list[dict[str, Any]]:
        """Reconcile every reserved intent before the worker accepts new ones."""
        outcomes: list[dict[str, Any]] = []
        for intent in self.unresolved_intents():
            quote = self.get_quote(intent.quote_id)
            now = self.clock.now()
            expired = now >= parse_utc(intent.expires_at_utc)
            if quote is None:
                self._close_intent(intent.intent_id, IntentStatus.RECONCILED_UNKNOWN, "missing_quote")
                self.latch_halt(HaltReason.SETTLEMENT_UNKNOWN)
                outcomes.append({"intent_id": intent.intent_id, "status": "RECONCILED_UNKNOWN"})
                continue
            if expired:
                self.expire_intent(intent.intent_id, "restart_expired")
                outcomes.append({"intent_id": intent.intent_id, "status": "EXPIRED"})
                continue
            fill = simulate_fill(
                fill_id=self.next_id("fill"),
                intent_id=intent.intent_id,
                quote=quote,
                offer_units=intent.offer_units,
                filled_at_utc=format_utc(now),
                settings=self.settings,
                mid_usdt_per_gram_micro=quote.usdt_per_gram_micro,
            )
            self.save_fill(fill, intent.direction)
            outcomes.append({"intent_id": intent.intent_id, "status": "FILLED_PAPER", "fill_id": fill.fill_id})
        self.set_control("recovered", "1")
        return outcomes

    def create_paper_intent(self, candidate: Candidate, quote: Quote, decision: RiskDecision) -> Intent:
        now = self.clock.now()
        intent = Intent(
            intent_id=self.next_id("int"),
            wallet_id=self.settings.paper_wallet_id,
            candidate_id=candidate.candidate_id,
            quote_id=quote.quote_id,
            policy_version=decision.policy_version,
            decision_id=decision.decision_id,
            direction=candidate.direction,
            offer_units=candidate.offer_units,
            reserved_usdt_micro=decision.reserved_usdt_micro,
            status=IntentStatus.RESERVED,
            created_at_utc=format_utc(now),
            expires_at_utc=format_utc(now + timedelta(seconds=self.settings.intent_ttl_s)),
            notes="paper_reserved",
        )
        self.reserve_intent(intent)
        return intent

    def settle_due_intents(self, mid_usdt_per_gram_micro: int) -> list[PaperFill]:
        fills: list[PaperFill] = []
        now = self.clock.now()
        for intent in self.unresolved_intents():
            created = parse_utc(intent.created_at_utc)
            due = created + timedelta(seconds=self.settings.delay_stress_s)
            if now < due:
                continue
            quote = self.get_quote(intent.quote_id)
            if quote is None:
                self._close_intent(intent.intent_id, IntentStatus.RECONCILED_UNKNOWN, "missing_quote")
                self.latch_halt(HaltReason.SETTLEMENT_UNKNOWN)
                continue
            if now >= parse_utc(intent.expires_at_utc):
                self.expire_intent(intent.intent_id, "ttl_expired")
                continue
            fill = simulate_fill(
                fill_id=self.next_id("fill"),
                intent_id=intent.intent_id,
                quote=quote,
                offer_units=intent.offer_units,
                filled_at_utc=format_utc(now),
                settings=self.settings,
                mid_usdt_per_gram_micro=mid_usdt_per_gram_micro,
            )
            self.save_fill(fill, intent.direction)
            fills.append(fill)
        return fills

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots(snapshot_id, as_of_utc, policy_version, payload_json) VALUES (?, ?, ?, ?)",
            (
                snapshot["snapshot_id"],
                snapshot["as_of_utc"],
                snapshot["policy_version"],
                _json(snapshot),
            ),
        )
        self.conn.commit()

    def latest_snapshot(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT payload_json FROM snapshots ORDER BY as_of_utc DESC LIMIT 1").fetchone()
        return json.loads(row["payload_json"]) if row else None

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def record_assessment(self, snapshot_id: str | None, status: AssessmentRecordStatus, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO assessments(received_at_utc, snapshot_id, record_status, payload_json) VALUES (?, ?, ?, ?)",
            (format_utc(self.clock.now()), snapshot_id, status.value, _json(payload)),
        )
        self.conn.commit()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("quotes", "candidates", "risk_decisions", "intents", "fills", "snapshots", "assessments"):
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[table] = int(row["n"])
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM risk_decisions WHERE verdict = ?",
            (RiskVerdict.APPROVE.value),
        ).fetchone()
        out["approvals"] = int(row["n"])
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM risk_decisions WHERE verdict = ?",
            (RiskVerdict.REJECT.value),
        ).fetchone()
        out["rejections"] = int(row["n"])
        return out

    def day_rows(self, day_utc: str) -> dict[str, Iterable[sqlite3.Row]]:
        return {
            "candidates": self.conn.execute(
                "SELECT * FROM candidates WHERE created_at_utc LIKE ?", (f"{day_utc}%",)
            ).fetchall(),
            "decisions": self.conn.execute(
                "SELECT * FROM risk_decisions WHERE created_at_utc LIKE ?", (f"{day_utc}%",)
            ).fetchall(),
            "intents": self.conn.execute(
                "SELECT * FROM intents WHERE created_at_utc LIKE ?", (f"{day_utc}%",)
            ).fetchall(),
            "fills": self.conn.execute(
                "SELECT * FROM fills WHERE filled_at_utc LIKE ?", (f"{day_utc}%",)
            ).fetchall(),
        }

    def save_daily_report(self, day_utc: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO daily_reports(day_utc, created_at_utc, payload_json) VALUES (?, ?, ?)",
            (day_utc, format_utc(self.clock.now()), _json(payload)),
        )
        self.conn.commit()

    def recent_alert(self, alert_key: str, within_s: int) -> bool:
        cutoff = format_utc(self.clock.now() - timedelta(seconds=within_s))
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE alert_key = ? AND sent_at_utc >= ? LIMIT 1",
            (alert_key, cutoff),
        ).fetchone()
        return row is not None

    def record_alert(self, alert_key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO alerts(alert_key, sent_at_utc) VALUES (?, ?)",
            (alert_key, format_utc(self.clock.now())),
        )
        self.conn.commit()

    def record_heartbeat(self) -> None:
        self.set_control("last_heartbeat_utc", format_utc(self.clock.now()))

    def last_heartbeat_utc(self) -> str:
        return self.get_control("last_heartbeat_utc")

    def record_feed_samples(self, feed_health: dict[str, Any], quote_age_ms: int | None) -> None:
        now = format_utc(self.clock.now())
        for name, health in feed_health.items():
            self.conn.execute(
                "INSERT INTO feed_samples(as_of_utc, name, status, latency_ms, quote_age_ms) VALUES (?, ?, ?, ?, ?)",
                (
                    now,
                    name,
                    str((health or {}).get("status") or "UNKNOWN"),
                    (health or {}).get("latency_ms"),
                    quote_age_ms,
                ),
            )
        self.conn.commit()

    def save_gas_profile(self, profile: dict[str, Any]) -> None:
        self.set_control("gas_profile_json", _json(profile))

    def load_gas_profile(self) -> dict[str, Any] | None:
        raw = self.get_control("gas_profile_json")
        if not raw:
            return None
        return json.loads(raw)

    def get_intent(self, intent_id: str) -> Intent | None:
        row = self.conn.execute("SELECT * FROM intents WHERE intent_id = ?", (intent_id,)).fetchone()
        return _intent_from_row(row) if row else None

    def fills_for_intent(self, intent_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM fills WHERE intent_id = ?", (intent_id,)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def halt_events_for_day(self, day_utc: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM halt_events WHERE as_of_utc LIKE ? ORDER BY as_of_utc",
            (f"{day_utc}%",),
        ).fetchall()
        return [dict(row) for row in rows]

    def feed_samples_for_day(self, day_utc: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM feed_samples WHERE as_of_utc LIKE ?",
            (f"{day_utc}%",),
        ).fetchall()
        return [dict(row) for row in rows]

    def quote_ages_ms_for_day(self, day_utc: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT quote_age_ms FROM feed_samples WHERE as_of_utc LIKE ? AND quote_age_ms IS NOT NULL",
            (f"{day_utc}%",),
        ).fetchall()
        return [int(row["quote_age_ms"]) for row in rows]

    def reset_paper(self, reason: str = "OPERATOR") -> dict[str, Any]:
        """Return paper balances to genesis. Does not touch drill.sqlite3."""
        now = format_utc(self.clock.now())
        mid = 0
        quotes = self.latest_quotes()
        if quotes:
            sample = next(iter(quotes.values()))
            mid = sample.usdt_per_gram_micro
        gram = self.settings.paper_gram_nano
        usdt = self.settings.paper_usdt_micro
        equity = mark_usdt_micro(gram, usdt, mid)
        for table in ("fills", "intents", "candidates", "risk_decisions", "events", "daily_reports"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute(
            """
            UPDATE positions SET
                as_of_utc=?, gram_nano=?, usdt_micro=?, reserved_usdt_micro=0,
                equity_usdt_micro=?, peak_equity_usdt_micro=?, daily_pnl_usdt_micro=0,
                realized_pnl_usdt_micro=0, day_utc=?
            WHERE id = 1
            """,
            (now, gram, usdt, equity, equity, now[:10]),
        )
        self.set_control("entry_halt", "0")
        self.set_control("halt_reason", "")
        self.set_control("recovered", "0")
        self.conn.execute(
            "INSERT INTO halt_events(as_of_utc, action, reason) VALUES (?, ?, ?)",
            (now, "reset", reason),
        )
        self.conn.commit()
        return {
            "reset": True,
            "reason": reason,
            "as_of_utc": now,
            "gram_nano": gram,
            "usdt_micro": usdt,
            "equity_usdt_micro": equity,
            "entry_halt": False,
            "fills": 0,
            "intents": 0,
        }


def _quote_from_payload(payload: dict[str, Any]) -> Quote:
    payload = dict(payload)
    payload["direction"] = Direction(payload["direction"])
    payload.pop("usdt_per_gram_micro", None)
    return Quote(**payload)


def _intent_from_row(row: sqlite3.Row) -> Intent:
    return Intent(
        intent_id=row["intent_id"],
        wallet_id=row["wallet_id"],
        candidate_id=row["candidate_id"],
        quote_id=row["quote_id"],
        policy_version=row["policy_version"],
        decision_id=row["decision_id"],
        direction=Direction(row["direction"]),
        offer_units=row["offer_units"],
        reserved_usdt_micro=row["reserved_usdt_micro"],
        status=IntentStatus(row["status"]),
        created_at_utc=row["created_at_utc"],
        expires_at_utc=row["expires_at_utc"],
        terminal_at_utc=row["terminal_at_utc"],
        notes=row["notes"],
    )
