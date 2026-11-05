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
    return sum(int(skips.get(k) or 0) for k in ("warming", "flat", "cooldown", "spread", "emitted", "approved", "fired"))


def evaluate_paper_day(report: dict[str, Any], *, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score one UTC day. Deterministic. This is the Evaluation box."""
    rules = rules or dict(PINNED_RULES)
    skips = report.get("signal_skips") or {}
    total = _skip_total(skips)
    flat = int(skips.get("flat") or 0)
    spread = int(skips.get("spread") or 0)
    emitted = int(skips.get("emitted") or skips.get("fired") or 0)
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
    rejection_total = sum(int(v or 0) for v in rejected.values()) if isinstance(rejected, dict) else 0

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
    if trades == 0 and emitted == 0:
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
            "approved": approved,
            "trades": trades,
            "realized_usdt_micro": realized,
            "scope": scope,
            "halt": halt,
            "halt_reason": halt_reason,
            "feed_uptime_floor_pct": feed_floor if feeds else None,
            "quote_age_p90_ms": quote_p90,
            "all_in_rejects": all_in_rejects,
            "rejection_total": rejection_total,
        },
        "rules_used": rules,
    }


def extract_lessons(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """Lessons box. Reusable, not a quote stream."""
    lessons: list[dict[str, Any]] = []
    sig = evaluation.get("signals") or {}
    total = int(sig.get("cycles") or 0)
    spread = int(sig.get("spread") or 0)
    flat = int(sig.get("flat") or 0)
    emitted = int(sig.get("emitted") or 0)
    trades = int(sig.get("trades") or 0)

    if total and spread * 2 > total:
        lessons.append(
            {
                "id": "spread_gate_too_tight",
                "severity": "high",
                "text": "Most cycles skipped as spread. Do not lower the 25 bps entry gate. Check mid_spread_max_bps vs live bid-ask.",
            }
        )
    if total and flat * 10 >= total * 8 and emitted == 0:
        lessons.append(
            {
                "id": "quiet_book_is_not_a_bug",
                "severity": "info",
                "text": "Book is flat vs SMA. That is the baseline working. Do not manufacture trades.",
            }
        )
    if trades:
        lessons.append(
            {
                "id": "audit_fills_after_costs",
                "severity": "info",
                "text": "Fills exist. Score net P&L after fee, impact, slippage, delay, and measured gas — not win rate.",
            }
        )
    if int(sig.get("all_in_rejects") or 0) and emitted:
        lessons.append(
            {
                "id": "all_in_cost_gate",
                "severity": "high",
                "text": "Risk rejected on all_in_cost. Inspect fee+impact+50bps slip vs max_all_in_cost_bps=80. Do not lower 25 bps.",
            }
        )
    for err in evaluation.get("errors") or []:
        if str(err).startswith("all_in_cost"):
            continue
        lessons.append({"id": "error", "severity": "high", "text": err})
    for gap in evaluation.get("gaps") or []:
        if gap not in {lesson["text"] for lesson in lessons}:
            lessons.append({"id": "gap", "severity": "low", "text": gap})
    if not lessons:
        lessons.append(
            {
                "id": "hold",
                "severity": "info",
                "text": "No rule change. Keep paper-only, outbound Telegram, and the 25 bps gate.",
            }
        )
    return lessons


def recommend_rules(evaluation: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Learning-update box. Advisory only. Never changes signing or entry deviation."""
    current = dict(current or PINNED_RULES)
    recommended = dict(current)
    notes: list[str] = []
    sig = evaluation.get("signals") or {}
    total = int(sig.get("cycles") or 0)
    spread = int(sig.get("spread") or 0)
    if total and spread * 2 > total and int(current.get("mid_spread_max_bps") or 0) < 200:
        recommended["mid_spread_max_bps"] = 200
        notes.append("raise mid_spread_max_bps to 200 so a healthy bid-ask is not skip=spread")
    recommended["signing"] = False
    recommended["broadcast"] = False
    recommended["strategy_entry_deviation_bps"] = int(PINNED_RULES["strategy_entry_deviation_bps"])
    recommended["max_all_in_cost_bps"] = int(current.get("max_all_in_cost_bps") or PINNED_RULES["max_all_in_cost_bps"])
    return {"recommended": recommended, "apply": False, "notes": notes}


def run_learn(
    ledger: Ledger,
    clock: Clock,
    day_utc: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """One closed loop: report (logs) → evaluate → lessons → advisory rules."""
    settings = settings or ledger.settings
    rules = rules_from_settings(settings)
    report = build_daily_report(ledger, clock, day_utc)
    evaluation = evaluate_paper_day(report, rules=rules)
    lessons = extract_lessons(evaluation)
    update = recommend_rules(evaluation, rules)
    payload = {
        "as_of_utc": format_utc(clock.now()),
        "day_utc": report.get("day_utc"),
        "mode": "PAPER",
        "evaluation": evaluation,
        "lessons": lessons,
        "learning_update": update,
        "notes": [
            "Deterministic closed loop. No model overlay.",
            "Rules are advisory. The worker does not auto-apply them.",
            "No signatures and no broadcasts were produced.",
        ],
    }
    ledger.set_control(LEARN_CONTROL_KEY, json.dumps(payload))
    return payload


def load_learn(ledger: Ledger) -> dict[str, Any] | None:
    raw = ledger.get_control(LEARN_CONTROL_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
