"""Closed learning loop for the paper worker. Logs → score → lessons → rules.

No LLM. No extra agents. Does not retune the 25 bps gate or touch signing.
Maps the Prompt Anatomy right-hand loop onto the live SQLite ledger.
"""

from __future__ import annotations

import json
from typing import Any

from ton_gram.clock import Clock, format_utc
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.report import build_daily_report

LEARN_CONTROL_KEY = "learn_last_json"

# Paper constraints. The orchestrator may *recommend* a rule change.
# It never writes these into Settings.
PINNED_RULES = {
    "mode": "PAPER",
    "signing": False,
    "broadcast": False,
    "strategy_entry_deviation_bps": 25,
    "mid_spread_max_bps": 200,
    "max_trade_usdt_micro": 50_000_000,
    "max_all_in_cost_bps": 80,
    "conservative_slippage_bps": 50,
    "intent_ttl_s": 45,
}

# Default 3% of the $10k funding cap. Matches Settings.drawdown_bps * funding_cap.
DRAWDOWN_FLOOR_USDT_MICRO = 300_000_000
# Default 1% of the $10k funding cap. Matches Settings.daily_loss_bps * funding_cap.
DAILY_LOSS_FLOOR_USDT_MICRO = 100_000_000


def rules_from_settings(settings: Settings | None = None) -> dict[str, Any]:
    """Live Settings for scoring. Signing stays false. 25 bps is not auto-lowered."""
    rules = dict(PINNED_RULES)
    if settings is None:
        return rules
    rules["strategy_entry_deviation_bps"] = int(settings.strategy_entry_deviation_bps)
    rules["mid_spread_max_bps"] = int(settings.mid_spread_max_bps)
    rules["max_trade_usdt_micro"] = int(settings.max_trade_usdt_micro)
    rules["max_all_in_cost_bps"] = int(settings.max_all_in_cost_bps)
    rules["conservative_slippage_bps"] = int(settings.conservative_slippage_bps)
    rules["intent_ttl_s"] = int(settings.intent_ttl_s)
    rules["signing"] = False
    rules["broadcast"] = False
    return rules


def _skip_total(skips: dict[str, Any]) -> int:
    return sum(
        int(skips.get(k) or 0)
        for k in ("warming", "flat", "cooldown", "spread", "feed_down", "halted", "emitted", "approved", "fired")
    )


def inventory_mark_dominates(realized_usdt_micro: int, unrealized_usdt_micro: int) -> bool:
    """True when marked inventory, not realized clips, is the daily-loss driver."""
    return unrealized_usdt_micro < 0 and unrealized_usdt_micro <= realized_usdt_micro


def evaluate_paper_day(report: dict[str, Any], *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score one UTC day. Deterministic. This is the Evaluation box."""
    rules = rules or dict(PINNED_RULES)
    skips = report.get("signal_skips") or {}
    total = _skip_total(skips)
    flat = int(skips.get("flat") or 0)
    spread = int(skips.get("spread") or 0)
    emitted = int(skips.get("emitted") or skips.get("fired") or 0)
    halted = int(skips.get("halted") or 0)
    feed_down = int(skips.get("feed_down") or 0)
    approved = int(skips.get("approved") or 0)
    trades = int(report.get("trades_count") or 0)
    halt = bool(report.get("entry_halt"))
    halt_reason = report.get("halt_reason")
    scope = report.get("scope_status") or "UNAVAILABLE"
    feeds = report.get("feed_uptime") or {}
    quote_p90 = int((report.get("quote_age") or {}).get("p90") or 0)
    realized = int(report.get("day_realized_pnl_usdt_micro") or 0)
    rejected = report.get("rejected_candidates_by_reason") or {}
    all_in_rejects = int(rejected.get("all_in_cost") or 0) if isinstance(rejected, dict) else 0
    daily_loss_rejects = int(rejected.get("daily_loss_limit") or 0) if isinstance(rejected, dict) else 0
    entry_halt_rejects = int(rejected.get("entry_halt") or 0) if isinstance(rejected, dict) else 0
    drawdown_rejects = int(rejected.get("drawdown_limit") or 0) if isinstance(rejected, dict) else 0
    rejection_total = sum(int(v or 0) for v in rejected.values()) if isinstance(rejected, dict) else 0
    unrealized = int(report.get("day_unrealized_pnl_usdt_micro") or 0)
    position = report.get("position") or {}
    drawdown = int(report.get("drawdown_usdt_micro") or position.get("drawdown_usdt_micro") or 0)
    realized_drawdown = int(
        report.get("realized_drawdown_usdt_micro")
        or position.get("realized_drawdown_usdt_micro")
        or 0
    )
    daily_pnl = int(position.get("daily_pnl_usdt_micro") or 0)
    if daily_pnl == 0 and (realized or unrealized):
        daily_pnl = realized + unrealized
    net_after_stressed = int(report.get("net_after_stressed_costs_usdt_micro") or realized)

    feed_floor = 100.0
    for stats in feeds.values() if isinstance(feeds, dict) else []:
        if isinstance(stats, dict) and stats.get("uptime_pct") is not None:
            feed_floor = min(feed_floor, float(stats["uptime_pct"]))

    errors: list[str] = []
    gaps: list[str] = []
    if halt and halt_reason == "SCOPE_MISMATCH":
        errors.append("halt_reason is SCOPE_MISMATCH; confirm it was not a feed-down overwrite")
    if halt and halt_reason == "HARD_HALT":
        errors.append("HARD_HALT is latched; do not rearm until the feed is up")
    if scope not in {"PASS", "UNAVAILABLE"}:
        errors.append(f"scope is {scope}")
    if feed_floor < 99:
        errors.append(f"feed uptime floor {feed_floor}%")
    if total and spread * 2 > total:
        errors.append("spread skips dominate the day; gate may be treating bid-ask as a broken book")
    if total == 0:
        gaps.append("no cycle skip samples yet")
    if trades == 0 and emitted == 0 and halted == 0:
        gaps.append("no candidate emitted; cannot audit fills")
    if emitted and not approved and trades == 0:
        gaps.append("candidates emitted but none approved")
    if emitted and all_in_rejects and (rejection_total == 0 or all_in_rejects * 2 >= rejection_total):
        errors.append(
            "all_in_cost rejects dominate; fee+impact+50bps slip exceeded max_all_in_cost_bps "
            "(default 80). Do not lower the 25 bps entry gate to compensate"
        )

    score = 100
    score -= 15 * len(errors)
    score -= 5 * len(gaps)
    if halt:
        score -= 20
    score = max(0, min(100, score))

    return {
        "score": score,
        "errors": errors,
        "gaps": gaps,
        "signals": {
            "cycles": total,
            "flat": flat,
            "spread": spread,
            "emitted": emitted,
            "halted": halted,
            "feed_down": feed_down,
            "approved": approved,
            "trades": trades,
            "realized_usdt_micro": realized,
            "scope": scope,
            "halt": halt,
            "halt_reason": halt_reason,
            "feed_uptime_floor_pct": feed_floor if feeds else None,
            "quote_age_p90_ms": quote_p90,
            "all_in_rejects": all_in_rejects,
            "daily_loss_rejects": daily_loss_rejects,
            "entry_halt_rejects": entry_halt_rejects,
            "drawdown_rejects": drawdown_rejects,
            "unrealized_usdt_micro": unrealized,
            "drawdown_usdt_micro": drawdown,
            "realized_drawdown_usdt_micro": realized_drawdown,
            "daily_pnl_usdt_micro": daily_pnl,
            "net_after_stressed_costs_usdt_micro": net_after_stressed,
            "rejection_total": rejection_total,
        },
        "rules_used": rules,
    }
