from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from ton_gram.clock import Clock, format_utc
from ton_gram.ledger import Ledger
from ton_gram.stats import summarize_ints

log = logging.getLogger("ton_gram.report")

_COUNT_TABLES = (
    "quotes",
    "candidates",
    "risk_decisions",
    "intents",
    "fills",
    "snapshots",
    "assessments",
)


def _counts(ledger: Ledger) -> dict[str, int]:
    """Count ledger rows without iterating a verdict string as SQLite bindings."""
    try:
        return ledger.counts()
    except Exception:
        log.exception("ledger.counts() failed; using safe fallback")
        out: dict[str, int] = {}
        for table in _COUNT_TABLES:
            row = ledger.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            out[table] = int(row["n"])
        for key, verdict in (("approvals", "APPROVE"), ("rejections", "REJECT")):
            row = ledger.conn.execute(
                "SELECT COUNT(*) AS n FROM risk_decisions WHERE verdict = ?",
                [verdict],
            ).fetchone()
            out[key] = int(row["n"])
        return out


def build_daily_report(ledger: Ledger, clock: Clock, day_utc: str | None = None) -> dict[str, Any]:
    day = day_utc or format_utc(clock.now())[:10]
    rows = ledger.day_rows(day)
    position = ledger.get_position()
    counts = _counts(ledger)
    fills = [dict(r) for r in rows["fills"]]
    decisions = [dict(r) for r in rows["decisions"]]
    intents = [dict(r) for r in rows["intents"]]
    candidates = [dict(r) for r in rows["candidates"]]

    trades: list[dict[str, Any]] = []
    fees_usdt_micro = 0
    network_cost_usdt_micro = 0
    gross_pnl = 0
    for row in fills:
        payload = json.loads(row["payload_json"]) if row.get("payload_json") else {}
        net_cost = int(payload.get("network_cost_usdt_micro") or 0)
        fee_units = int(payload.get("pool_fee_units") or 0)
        mid = int(payload.get("mid_usdt_per_gram_micro") or 0)
        offer = int(payload.get("offer_units") or 0)
        filled = int(payload.get("filled_ask_units") or 0)
        raw = int(payload.get("raw_ask_units") or 0)
        direction = None
        intent = next((i for i in intents if i["intent_id"] == row["intent_id"]), None)
        if intent:
            direction = intent["direction"]
        if direction == "GRAM_TO_USDT":
            notional = (offer * mid) // 1_000_000_000 if mid else raw
            gross = filled - notional
            fees_usdt_micro += fee_units
        else:
            received = (filled * mid) // 1_000_000_000 if mid else 0
            notional = offer
            gross = received - notional
            fees_usdt_micro += (fee_units * mid) // 1_000_000_000 if mid else 0
        network_cost_usdt_micro += net_cost
        gross_pnl += gross
        trades.append(
            {
                "fill_id": row["fill_id"],
                "intent_id": row["intent_id"],
                "filled_at_utc": row["filled_at_utc"],
                "direction": direction,
                "offer_units": offer,
                "filled_ask_units": filled,
                "gross_pnl_usdt_micro": gross,
                "network_cost_usdt_micro": net_cost,
                "net_pnl_usdt_micro": gross - net_cost,
            }
        )

    rejected_by_reason: Counter[str] = Counter()
    for decision in decisions:
        if decision["verdict"] != "REJECT":
            continue
        payload = json.loads(decision["payload_json"]) if decision.get("payload_json") else {}
        reasons = payload.get("reasons") or ["unspecified"]
        for reason in reasons:
            key = str(reason).split("=")[0]
            rejected_by_reason[key] += 1

    feed_rows = ledger.feed_samples_for_day(day)
    uptime: dict[str, Any] = {}
    by_feed: dict[str, list[str]] = {}
    for sample in feed_rows:
        by_feed.setdefault(sample["name"], []).append(sample["status"])
    for name, statuses in by_feed.items():
        up = sum(1 for s in statuses if s == "UP")
        uptime[name] = {
            "samples": len(statuses),
            "up": up,
            "uptime_pct": round(100.0 * up / len(statuses), 2) if statuses else 0.0,
        }

    ages = ledger.quote_ages_ms_for_day(day)
    profile = ledger.load_gas_profile() or {}
    report = {
        "day_utc": day,
        "as_of_utc": format_utc(clock.now()),
        "mode": "PAPER",
        "policy_version": ledger.settings.policy_version,
        "trades": trades,
        "trades_count": len(trades),
        "candidates": len(candidates),
        "risk_decisions": len(decisions),
        "approvals": sum(1 for d in decisions if d["verdict"] == "APPROVE"),
        "rejections": sum(1 for d in decisions if d["verdict"] == "REJECT"),
        "rejected_candidates_by_reason": dict(rejected_by_reason),
        "intents": len(intents),
        "paper_fills": len(fills),
        "unresolved_intents": len(ledger.unresolved_intents()),
        "gross_pnl_usdt_micro": gross_pnl,
        "fees_usdt_micro": fees_usdt_micro,
        "network_cost_usdt_micro": network_cost_usdt_micro,
        "net_pnl_usdt_micro": gross_pnl - network_cost_usdt_micro,
        "drawdown_usdt_micro": position.peak_equity_usdt_micro - position.equity_usdt_micro,
        "halts": ledger.halt_events_for_day(day),
        "feed_uptime": uptime,
        "quote_age": summarize_ints(ages),
        "cost_model": {
            "source": ledger.settings.cost_model_source,
            "delay_stress_s": ledger.settings.delay_stress_s,
            "delay_adverse_bps": ledger.settings.delay_adverse_bps,
            "measured_net_cost_p90_nano": ledger.settings.measured_net_cost_p90_nano,
            "profile": {
                "sample_n": profile.get("sample_n"),
                "net_cost_gram_nano": profile.get("net_cost_gram_nano"),
                "latency_s": profile.get("latency_s"),
                "applied": profile.get("applied"),
            },
        },
        "entry_halt": ledger.entry_halt,
        "halt_reason": ledger.halt_reason or None,
        "position": {
            "gram_nano": position.gram_nano,
            "usdt_micro": position.usdt_micro,
            "equity_usdt_micro": position.equity_usdt_micro,
            "daily_pnl_usdt_micro": position.daily_pnl_usdt_micro,
            "realized_pnl_usdt_micro": position.realized_pnl_usdt_micro,
            "peak_equity_usdt_micro": position.peak_equity_usdt_micro,
            "drawdown_usdt_micro": position.peak_equity_usdt_micro - position.equity_usdt_micro,
        },
        "lifetime_counts": counts,
        "notes": [
            "Baseline is deterministic mid-deviation only.",
            "Fills are simulated with stressed pool fee, impact, measured gas, and measured delay.",
            "No signatures and no broadcasts were produced.",
        ],
    }
    ledger.save_daily_report(day, report)
    return report
