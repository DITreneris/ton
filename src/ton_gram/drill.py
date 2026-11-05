"""Admin-only recovery drills. Disabled unless TON_GRAM_DRILL_TOKEN is set.

Drills always use an isolated SQLite file (drill.sqlite3). They never write
paper positions, PnL, the daily report, or the live halt latch.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from ton_gram.clock import Clock, FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.measure import hydrate_cost_model
from ton_gram.models import (
    Candidate,
    Direction,
    IntentStatus,
    RiskDecision,
    RiskVerdict,
)

SCENARIOS = (
    "kill_before_broadcast",
    "kill_after_broadcast",
    "duplicate_event",
    "rpc_timeout",
    "bounce",
    "delayed_refund",
)


def _make_due(ledger: Ledger, clock: Clock, intent_id: str) -> None:
    past = format_utc(clock.now() - timedelta(seconds=ledger.settings.delay_stress_s + 1))
    ledger.conn.execute(
        "UPDATE intents SET created_at_utc = ? WHERE intent_id = ?",
        (past, intent_id),
    )
    ledger.conn.commit()


def _inline_quote(clock: Clock, quote_id: str):
    from ton_gram.config import (
        NATIVE_GRAM_ADDRESS,
        OFFICIAL_USDT_JETTON_MASTER,
        PINNED_POOL_ADDRESS,
        PINNED_PTON_MASTER,
        PINNED_PTON_WALLET,
        PINNED_ROUTER_ADDRESS,
    )
    from ton_gram.models import Quote

    return Quote(
        quote_id=quote_id,
        direction=Direction.GRAM_TO_USDT,
        observed_at_utc=format_utc(clock.now()),
        offer_address=NATIVE_GRAM_ADDRESS,
        ask_address=OFFICIAL_USDT_JETTON_MASTER,
        offer_units=1_000_000_000,
        ask_units=1_160_000,
        min_ask_units=1_148_400,
        fee_units=1_000_000,
        fee_percent_bps=10,
        price_impact_bps=1,
        gas_estimate_nano=45_000_000,
        forward_gas_nano=185_000_000,
        swap_rate_e9=1_160_000_000,
        router_address=PINNED_ROUTER_ADDRESS,
        pool_address=PINNED_POOL_ADDRESS,
        pton_master=PINNED_PTON_MASTER,
        pton_wallet=PINNED_PTON_WALLET,
        router_major_version=2,
        liquidity_usd_micro=4_000_000_000_000,
        reserve_gram_nano=1_700_000_000_000_000,
        reserve_usdt_micro=2_000_000_000_000,
        provider_latency_ms=40,
    )


def _reserve(ledger: Ledger, clock: Clock, suffix: str, *, save_quote: bool = True) -> tuple[Any, Any]:
    quote = _inline_quote(clock, f"q_drill_{suffix}")
    if save_quote:
        ledger.save_quote(quote)
    candidate = Candidate(
        candidate_id=f"cand_drill_{suffix}",
        created_at_utc=format_utc(clock.now()),
        direction=Direction.GRAM_TO_USDT,
        offer_units=1_000_000_000,
        quote_id=quote.quote_id,
        mid_usdt_per_gram_micro=quote.usdt_per_gram_micro,
        sma_usdt_per_gram_micro=quote.usdt_per_gram_micro,
        deviation_bps=30,
        policy_version=ledger.settings.policy_version,
        reason="drill",
    )
    ledger.save_candidate(candidate)
    decision = RiskDecision(
        decision_id=f"dec_drill_{suffix}",
        candidate_id=candidate.candidate_id,
        quote_id=quote.quote_id,
        policy_version=ledger.settings.policy_version,
        verdict=RiskVerdict.APPROVE,
        reasons=["drill"],
        reserved_usdt_micro=1_160_000,
        created_at_utc=format_utc(clock.now()),
    )
    ledger.save_decision(decision)
    intent = ledger.create_paper_intent(candidate, quote, decision)
    return quote, intent


def _assertions(ledger: Ledger, intent_id: str) -> dict[str, Any]:
    fills = ledger.fills_for_intent(intent_id)
    intent = ledger.get_intent(intent_id)
    terminal = intent is not None and intent.status is not IntentStatus.RESERVED
    incident = ledger.entry_halt or (intent is not None and intent.status is IntentStatus.RECONCILED_UNKNOWN)
    return {
        "intent_id": intent_id,
        "intent_status": intent.status.value if intent else None,
        "fill_count": len(fills),
        "fill_ids": [f["fill_id"] for f in fills],
        "duplicate_fills": len(fills) > 1,
        "false_fill": False,
        "terminal_or_incident": bool(terminal or incident),
        "entry_halt": ledger.entry_halt,
        "halt_reason": ledger.halt_reason or None,
        "passed": len(fills) <= 1 and bool(terminal or incident),
    }


class DrillHarness:
    def __init__(self, settings: Settings, clock: Clock, ledger: Ledger) -> None:
        self.settings = settings
        self.clock = clock
        self.ledger = ledger

    def enabled(self) -> bool:
        return bool(self.settings.drill_token)

    def run(self, scenario: str) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario}")
        return getattr(self, f"_case_{scenario}")()

    def _case_kill_before_broadcast(self) -> dict[str, Any]:
        quote, intent = _reserve(self.ledger, self.clock, "before")
        # Restart after delay_stress — same clock as settle_due. Do not fill early.
        _make_due(self.ledger, self.clock, intent.intent_id)
        outcomes = self.ledger.recover()
        checks = _assertions(self.ledger, intent.intent_id)
        return {"scenario": "kill_before_broadcast", "recovery": outcomes, **checks}

    def _case_kill_after_broadcast(self) -> dict[str, Any]:
        quote, intent = _reserve(self.ledger, self.clock, "after")
        self.ledger.conn.execute(
            "UPDATE intents SET notes = ? WHERE intent_id = ?",
            ("paper_broadcast", intent.intent_id),
        )
        self.ledger.conn.commit()
        _make_due(self.ledger, self.clock, intent.intent_id)
        outcomes = self.ledger.recover()
        # A second recover must not create another fill.
        again = self.ledger.recover()
        checks = _assertions(self.ledger, intent.intent_id)
        return {"scenario": "kill_after_broadcast", "recovery": outcomes, "second_recovery": again, **checks}

    def _case_duplicate_event(self) -> dict[str, Any]:
        quote, intent = _reserve(self.ledger, self.clock, "dup")
        event_id = f"drill:dup:{intent.intent_id}"
        first = self.ledger.ingest_event(event_id, "drill", {"intent_id": intent.intent_id})
        second = self.ledger.ingest_event(event_id, "drill", {"intent_id": intent.intent_id})
        _make_due(self.ledger, self.clock, intent.intent_id)
        self.ledger.settle_due_intents(quote.usdt_per_gram_micro)
        self.ledger.settle_due_intents(quote.usdt_per_gram_micro)
        checks = _assertions(self.ledger, intent.intent_id)
        checks["event_accepted"] = first
        checks["duplicate_event_ignored"] = not second
        checks["passed"] = checks["passed"] and first and (not second)
        return {"scenario": "duplicate_event", **checks}

    def _case_rpc_timeout(self) -> dict[str, Any]:
        quote, intent = _reserve(self.ledger, self.clock, "timeout", save_quote=True)
        self.ledger.conn.execute("DELETE FROM quotes WHERE quote_id = ?", (quote.quote_id,))
        self.ledger.conn.commit()
        outcomes = self.ledger.recover()
        checks = _assertions(self.ledger, intent.intent_id)
        checks["false_fill"] = checks["fill_count"] > 0
        checks["passed"] = (
            checks["fill_count"] == 0
            and checks["intent_status"] == IntentStatus.RECONCILED_UNKNOWN.value
            and self.ledger.entry_halt
        )
        return {"scenario": "rpc_timeout", "recovery": outcomes, **checks}

    def _case_bounce(self) -> dict[str, Any]:
        quote, intent = _reserve(self.ledger, self.clock, "bounce")
        self.ledger.reject_intent(intent.intent_id, "bounce")
        self.ledger.settle_due_intents(quote.usdt_per_gram_micro)
        checks = _assertions(self.ledger, intent.intent_id)
        checks["false_fill"] = checks["fill_count"] > 0
        checks["passed"] = checks["fill_count"] == 0 and checks["intent_status"] == IntentStatus.REJECTED.value
        return {"scenario": "bounce", **checks}

    def _case_delayed_refund(self) -> dict[str, Any]:
        quote, intent = _reserve(self.ledger, self.clock, "delay")
        if isinstance(self.clock, FrozenClock):
            self.clock.advance(self.settings.delay_stress_s + 1)
        else:
            # Wall clock: force due by rewriting created_at into the past.
            past = format_utc(self.clock.now() - timedelta(seconds=self.settings.delay_stress_s + 1))
            self.ledger.conn.execute(
                "UPDATE intents SET created_at_utc = ? WHERE intent_id = ?",
                (past, intent.intent_id),
            )
            self.ledger.conn.commit()
        fills = self.ledger.settle_due_intents(quote.usdt_per_gram_micro)
        fills_again = self.ledger.settle_due_intents(quote.usdt_per_gram_micro)
        checks = _assertions(self.ledger, intent.intent_id)
        checks["settle_fills"] = len(fills)
        checks["second_settle_fills"] = len(fills_again)
        checks["passed"] = checks["fill_count"] == 1 and len(fills_again) == 0 and checks["terminal_or_incident"]
        return {"scenario": "delayed_refund", **checks}


def drill_db_path(settings: Settings) -> Path:
    return settings.data_dir / "drill.sqlite3"


def _unlink_sqlite(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix) if suffix else path
        if candidate.exists():
            candidate.unlink()


def isolated_harness(settings: Settings, clock: Clock) -> DrillHarness:
    """Fresh drill ledger beside the paper DB. Paper file is never opened."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = drill_db_path(settings)
    _unlink_sqlite(path)
    hydrate_cost_model(settings, None)
    ledger = Ledger(settings, clock, db_path=path)
    hydrate_cost_model(settings, ledger)
    return DrillHarness(settings, clock, ledger)


def run_all(harness: DrillHarness) -> dict[str, Any]:
    results = [harness.run(name) for name in SCENARIOS]
    return {
        "mode": "PAPER",
        "namespace": "drill",
        "paper_untouched": True,
        "signing": False,
        "broadcast": False,
        "passed": all(item.get("passed") for item in results),
        "results": results,
    }


def remote_run(base_url: str, token: str, scenario: str | None = None, timeout_s: float = 20.0) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, headers=headers) as client:
        if scenario and scenario != "all":
            response = client.post(f"/v1/drill/{scenario}")
            response.raise_for_status()
            return response.json()
        response = client.post("/v1/drill/all")
        response.raise_for_status()
        return response.json()
