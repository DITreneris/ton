"""Remainder of Ledger methods. Installed onto Ledger at import."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any, Iterable

from ton_gram.clock import format_utc, parse_utc
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


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)

def mark_to_market(self, mid_usdt_per_gram_micro: int) -> Position:
    position = self.get_position()
    now = format_utc(self.clock.now())
    day = now[:10]
    equity = mark_usdt_micro(position.gram_nano, position.usdt_micro, mid_usdt_per_gram_micro)
    if day != position.day_utc:
        position.daily_pnl_usdt_micro = 0
        position.day_realized_pnl_usdt_micro = 0
        position.day_utc = day
        sod_equity = equity
    else:
        sod_equity = equity - position.daily_pnl_usdt_micro
        # keep daily as equity - start-of-day; start-of-day inferred from previous daily
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
    old_realized = position.realized_pnl_usdt_micro
    realized = old_realized - fill.network_cost_usdt_micro
    if direction is Direction.GRAM_TO_USDT:
        realized += fill.filled_ask_units - (
            (intent.offer_units * fill.mid_usdt_per_gram_micro) // 1_000_000_000
        )
    else:
        received = (fill.filled_ask_units * fill.mid_usdt_per_gram_micro) // 1_000_000_000
        realized += received - intent.offer_units
    delta = realized - old_realized
    position.gram_nano = gram
    position.usdt_micro = usdt
    position.realized_pnl_usdt_micro = realized
    self._write_position(position)
    self._close_intent(fill.intent_id, IntentStatus.FILLED_PAPER, "paper_fill")
    position = self.mark_to_market(fill.mid_usdt_per_gram_micro)
    position.day_realized_pnl_usdt_micro += delta
    clip = self.settings.funding_cap_usdt_micro + position.realized_pnl_usdt_micro
    position.peak_clip_equity_usdt_micro = max(position.peak_clip_equity_usdt_micro, clip)
    self._write_position(position)
    return position

def expire_intent(self, intent_id: str, notes: str) -> None:
    self._close_intent(intent_id, IntentStatus.EXPIRED, notes)

def reject_intent(self, intent_id: str, notes: str) -> None:
    self._close_intent(intent_id, IntentStatus.REJECTED, notes)

def recover(self) -> list[dict[str, Any]]:
    """Reconcile reserved intents. Same delay/TTL clock as settle_due_intents.

    Missing quote → SETTLEMENT_UNKNOWN. TTL lapse → EXPIRED (no halt).
    Delay not elapsed → leave RESERVED for settle_due. Do not fill early.
    """
    outcomes: list[dict[str, Any]] = []
    now = self.clock.now()
    for intent in self.unresolved_intents():
        quote = self.get_quote(intent.quote_id)
        expired = now >= parse_utc(intent.expires_at_utc)
        due = parse_utc(intent.created_at_utc) + timedelta(seconds=self.settings.delay_stress_s)
        if quote is None:
            self._close_intent(intent.intent_id, IntentStatus.RECONCILED_UNKNOWN, "missing_quote")
            self.latch_halt(HaltReason.SETTLEMENT_UNKNOWN)
            outcomes.append({"intent_id": intent.intent_id, "status": "RECONCILED_UNKNOWN"})
            continue
        if expired:
            self.expire_intent(intent.intent_id, "restart_expired")
            outcomes.append({"intent_id": intent.intent_id, "status": "EXPIRED"})
            continue
        if now < due:
            outcomes.append({"intent_id": intent.intent_id, "status": "PENDING_DELAY"})
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


def install(Ledger) -> None:
    import ton_gram.ledger as ledger_mod
    from ton_gram import ledger_ext2
    global _intent_from_row, _quote_from_payload
    _intent_from_row = ledger_ext2._intent_from_row
    _quote_from_payload = ledger_ext2._quote_from_payload
    for name in (
        "mark_to_market",
        "unresolved_intents",
        "reserve_intent",
        "_close_intent",
        "save_fill",
        "expire_intent",
        "reject_intent",
        "recover",
        "create_paper_intent",
        "settle_due_intents",
    ):
        setattr(Ledger, name, globals()[name])
    ledger_ext2.install(Ledger, ledger_mod)
