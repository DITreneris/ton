from __future__ import annotations

from typing import Any

from ton_gram.clock import Clock, format_utc
from ton_gram.ledger import Ledger


def build_daily_report(ledger: Ledger, clock: Clock, day_utc: str | None = None) -> dict[str, Any]:
    day = day_utc or format_utc(clock.now())[:10]
    rows = ledger.day_rows(day)
    position = ledger.get_position()
    counts = ledger.counts()
    fills = [dict(r) for r in rows["fills"]]
    decisions = [dict(r) for r in rows["decisions"]]
    intents = [dict(r) for r in rows["intents"]]
    report = {
        "day_utc": day,
        "as_of_utc": format_utc(clock.now()),
        "mode": "PAPER",
        "policy_version": ledger.settings.policy_version,
        "candidates": len(list(rows["candidates"])),
        "risk_decisions": len(decisions),
        "approvals": sum(1 for d in decisions if d["verdict"] == "APPROVE"),
        "rejections": sum(1 for d in decisions if d["verdict"] == "REJECT"),
        "intents": len(intents),
        "paper_fills": len(fills),
        "unresolved_intents": len(ledger.unresolved_intents()),
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
            "Fills are simulated with stressed pool fee, impact, gas, slippage, and delay.",
            "No signatures and no broadcasts were produced.",
        ],
    }
    ledger.save_daily_report(day, report)
    return report
