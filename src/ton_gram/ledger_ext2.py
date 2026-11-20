"""More Ledger methods installed onto Ledger at import."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any, Iterable

from ton_gram.clock import format_utc, parse_utc
from ton_gram.models import (
    AssessmentRecordStatus,
    Direction,
    Intent,
    IntentStatus,
    Quote,
    RiskVerdict,
)
from ton_gram.units import mark_usdt_micro


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)

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
    for key, verdict in (
        ("approvals", RiskVerdict.APPROVE.value),
        ("rejections", RiskVerdict.REJECT.value),
    ):
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM risk_decisions WHERE verdict = ?",
            [verdict],
        ).fetchone()
        out[key] = int(row["n"])
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

def save_strategy_state(self, payload: dict[str, Any]) -> None:
    self.set_control("strategy_state_json", _json(payload))

def load_strategy_state(self) -> dict[str, Any] | None:
    raw = self.get_control("strategy_state_json")
    if not raw:
        return None
    return json.loads(raw)

def load_signal_skips(self, day_utc: str) -> dict[str, int]:
    empty = {
        "warming": 0,
        "flat": 0,
        "cooldown": 0,
        "spread": 0,
        "feed_down": 0,
        "halted": 0,
        "emitted": 0,
        "approved": 0,
    }
    raw = self.get_control(f"signal_skips_{day_utc}")
    if not raw:
        return empty
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return empty
    out = dict(empty)
    for key, value in data.items():
        out[str(key)] = int(value or 0)
    return out

def bump_signal_skip(self, day_utc: str, skip: str | None) -> dict[str, int]:
    counts = self.load_signal_skips(day_utc)
    bucket = skip or "emitted"
    counts[bucket] = int(counts.get(bucket) or 0) + 1
    self.set_control(f"signal_skips_{day_utc}", json.dumps(counts))
    return counts

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
    cap = int(self.settings.funding_cap_usdt_micro)
    for table in ("fills", "intents", "candidates", "risk_decisions", "events", "daily_reports"):
        self.conn.execute(f"DELETE FROM {table}")
    self.conn.execute(
        """
        UPDATE positions SET
            as_of_utc=?, gram_nano=?, usdt_micro=?, reserved_usdt_micro=0,
            equity_usdt_micro=?, peak_equity_usdt_micro=?, daily_pnl_usdt_micro=0,
            realized_pnl_usdt_micro=0, day_realized_pnl_usdt_micro=0,
            peak_clip_equity_usdt_micro=?, day_utc=?
        WHERE id = 1
        """,
        (now, gram, usdt, equity, equity, cap, now[:10]),
    )
    self.set_control("entry_halt", "0")
    self.set_control("halt_reason", "")
    self.set_control("recovered", "0")
    self.set_control("strategy_state_json", "")
    self.set_control("learn_last_json", "")
    stale_skips = self.conn.execute(
        "SELECT k FROM control_state WHERE k LIKE 'signal_skips_%'"
    ).fetchall()
    for row in stale_skips:
        self.conn.execute("DELETE FROM control_state WHERE k = ?", (row["k"],))
    self.conn.commit()
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


def install(Ledger, ledger_mod) -> None:
    for name in (
        "save_snapshot",
        "latest_snapshot",
        "get_snapshot",
        "record_assessment",
        "counts",
        "day_rows",
        "save_daily_report",
        "recent_alert",
        "record_alert",
        "record_heartbeat",
        "last_heartbeat_utc",
        "record_feed_samples",
        "save_gas_profile",
        "load_gas_profile",
        "save_strategy_state",
        "load_strategy_state",
        "load_signal_skips",
        "bump_signal_skip",
        "get_intent",
        "fills_for_intent",
        "halt_events_for_day",
        "feed_samples_for_day",
        "quote_ages_ms_for_day",
        "reset_paper",
    ):
        setattr(Ledger, name, globals()[name])
    ledger_mod._quote_from_payload = _quote_from_payload
    ledger_mod._intent_from_row = _intent_from_row
    globals()["_intent_from_row"]  # keep
