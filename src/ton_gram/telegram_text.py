"""Outbound Telegram copy. One daily card. Immediate halt cards. No inbound commands."""

from __future__ import annotations

from typing import Any

from ton_gram.units import USDT_SCALE


def format_usd(micro: int) -> str:
    sign = "-" if micro < 0 else ""
    mag = abs(int(micro))
    dollars = mag // USDT_SCALE
    cents = (mag % USDT_SCALE) // 10_000
    if micro > 0:
        sign = "+"
    return f"{sign}${dollars}.{cents:02d}"


def format_halt_card(reason: str, detail: str | None = None) -> str:
    lines = [
        "ENTRY HALTED",
        "",
        f"Reason: {reason or 'LATCHED'}",
        "",
        "New entries blocked.",
        "Manual rearm required on the Railway instance",
        "(POST /v1/control/rearm with bearer).",
        "Do not rearm until you know why it stopped.",
    ]
    if detail:
        lines.extend(["", detail[:400]])
    return "\n".join(lines)


def format_daily_card(report: dict[str, Any], *, funding_cap_usdt_micro: int) -> str:
    day = report.get("day_utc") or ""
    halt = bool(report.get("entry_halt"))
    reason = report.get("halt_reason") or "LATCHED"
    status = f"ENTRY HALTED ({reason})" if halt else "RUNNING"
    scope = report.get("scope_status") or "UNAVAILABLE"
    cap = funding_cap_usdt_micro or 1
    drawdown = int(report.get("drawdown_usdt_micro") or 0)
    drawdown_pct = (drawdown * 10_000) // cap
    drawdown_pct_text = f"{drawdown_pct // 100}.{drawdown_pct % 100:02d}%"
    skips = report.get("signal_skips") or {}
    rejected = report.get("rejected_candidates_by_reason") or {}
    reject_n = sum(int(v) for v in rejected.values()) if isinstance(rejected, dict) else 0
    feeds = report.get("feed_uptime") or {}
    feed_bits: list[str] = []
    if isinstance(feeds, dict):
        for name, stats in feeds.items():
            if not isinstance(stats, dict):
                continue
            pct = stats.get("uptime_pct")
            status_bit = "UP" if float(pct or 0) >= 99 else "DEGRADED"
            feed_bits.append(f"{name} {status_bit} {pct}%")
    feed_line = ", ".join(feed_bits) if feed_bits else "no samples"
    quote_age = report.get("quote_age") or {}
    p90 = quote_age.get("p90") if isinstance(quote_age, dict) else None
    skip_line = (
        f"flat {int(skips.get('flat') or 0)} / "
        f"warming {int(skips.get('warming') or 0)} / "
        f"cooldown {int(skips.get('cooldown') or 0)} / "
        f"spread {int(skips.get('spread') or 0)} / "
        f"fired {int(skips.get('fired') or 0)}"
    )
    return "\n".join(
        [
            "Daily paper report",
            day,
            "",
            "Mode: PAPER",
            f"Status: {status}",
            f"Scope: {scope}",
            f"Trades: {int(report.get('trades_count') or 0)}",
            f"Gross P&L: {format_usd(int(report.get('gross_pnl_usdt_micro') or 0))}",
            f"Fees: {format_usd(int(report.get('fees_usdt_micro') or 0))}",
            f"Network cost: {format_usd(int(report.get('network_cost_usdt_micro') or 0))}",
            f"Net P&L: {format_usd(int(report.get('day_realized_pnl_usdt_micro') or report.get('net_pnl_usdt_micro') or 0))}",
            f"Unrealized (mark): {format_usd(int(report.get('day_unrealized_pnl_usdt_micro') or 0))}",
            f"Drawdown: {format_usd(drawdown)} ({drawdown_pct_text} of ${cap // USDT_SCALE} cap)",
            f"Candidates / rejected: {int(report.get('candidates') or 0)} / {reject_n}",
            f"Signal: {skip_line}",
            f"Feed: {feed_line}",
            f"Quote age p90: {p90 if p90 is not None else 0} ms",
        ]
    )
