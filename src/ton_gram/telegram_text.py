"""Outbound Telegram copy. One daily card. Immediate halt cards. No inbound commands."""

from __future__ import annotations

from typing import Any

from ton_gram.units import USDT_SCALE


def format_usd(micro: int, *, signed: bool = True) -> str:
    if micro < 0:
        sign = "-"
    elif micro > 0 and signed:
        sign = "+"
    else:
        sign = ""
    mag = abs(int(micro))
    dollars = mag // USDT_SCALE
    cents = (mag % USDT_SCALE) // 10_000
    return f"{sign}${dollars}.{cents:02d}"


def _int_field(payload: dict[str, Any], *names: str, default: int = 0) -> int:
    for name in names:
        if name in payload and payload[name] is not None:
            return int(payload[name])
    return default


def format_halt_card(reason: str, detail: str | None = None) -> str:
    lines = [
        "ENTRY HALTED",
        "",
        f"Reason: {reason or 'LATCHED'}",
        "",
        "New entries blocked.",
        "Manual rearm required on the running instance",
        "(Railway: POST /v1/control/rearm with bearer).",
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
    realized = report.get("day_realized_pnl_usdt_micro")
    if realized is None:
        realized = report.get("net_pnl_usdt_micro")
    unrealized = report.get("day_unrealized_pnl_usdt_micro")
    if unrealized is None:
        unrealized = 0
    emitted = int(skips.get("emitted") or skips.get("fired") or 0)
    skip_line = (
        f"flat {int(skips.get('flat') or 0)} / "
        f"warming {int(skips.get('warming') or 0)} / "
        f"cooldown {int(skips.get('cooldown') or 0)} / "
        f"spread {int(skips.get('spread') or 0)} / "
        f"feed_down {int(skips.get('feed_down') or 0)} / "
        f"emitted {emitted} / "
        f"approved {int(skips.get('approved') or 0)}"
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
            f"Gross P&L: {format_usd(_int_field(report, 'gross_pnl_usdt_micro'))}",
            f"Fees: {format_usd(_int_field(report, 'fees_usdt_micro'))}",
            f"Network cost: {format_usd(_int_field(report, 'network_cost_usdt_micro'))}",
            f"Net P&L: {format_usd(int(realized) if realized is not None else 0)}",
            f"Unrealized (mark): {format_usd(int(unrealized))}",
            f"Drawdown: {format_usd(drawdown, signed=False)} ({drawdown_pct_text} of ${cap // USDT_SCALE} cap)",
            f"Candidates / rejected: {int(report.get('candidates') or 0)} / {reject_n}",
            f"Signal: {skip_line}",
            f"Feed: {feed_line}",
            f"Quote age p90: {p90 if p90 is not None else 0} ms",
        ]
    )
