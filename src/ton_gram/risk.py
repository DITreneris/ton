"""Risk limits. A rejected candidate produces zero intents."""

from __future__ import annotations

from ton_gram.clock import Clock, format_utc, parse_utc
from ton_gram.config import Settings
from ton_gram.models import (
    Candidate,
    Direction,
    Intent,
    Position,
    Quote,
    RiskDecision,
    RiskVerdict,
    ScopeVerification,
)
from ton_gram.units import apply_bps, gram_from_usdt, usdt_per_gram_micro


def quote_age_ms(quote: Quote, clock: Clock) -> int:
    age = clock.now() - parse_utc(quote.observed_at_utc)
    return int(age.total_seconds() * 1000)


def notional_usdt_micro(candidate: Candidate, quote: Quote) -> int:
    if candidate.direction is Direction.USDT_TO_GRAM:
        return candidate.offer_units
    mid = quote.usdt_per_gram_micro or candidate.mid_usdt_per_gram_micro
    return usdt_per_gram_micro(candidate.offer_units, mid)


def evaluate_risk(
    *,
    settings: Settings,
    clock: Clock,
    candidate: Candidate,
    quote: Quote,
    position: Position,
    scope: ScopeVerification,
    entry_halt: bool,
    unresolved_intents: list[Intent],
    decision_id: str,
) -> RiskDecision:
    reasons: list[str] = []
    if not scope.passed:
        reasons.append("scope_verification_failed")
    if entry_halt:
        reasons.append("entry_halt")
    if candidate.policy_version != settings.policy_version:
        reasons.append("policy_version_mismatch")
    if quote.quote_id != candidate.quote_id:
        reasons.append("quote_binding_mismatch")
    age = quote_age_ms(quote, clock)
    if age > settings.max_quote_age_ms:
        reasons.append(f"stale_quote_age_ms={age}")
    if quote.liquidity_usd_micro < settings.min_liquidity_usd_micro:
        reasons.append("min_liquidity")
    if quote.router_major_version != settings.router_major_version:
        reasons.append("wrong_router_version")

    notional = notional_usdt_micro(candidate, quote)
    cap = apply_bps(settings.funding_cap_usdt_micro, settings.position_cap_bps)
    reserved = position.reserved_usdt_micro + sum(i.reserved_usdt_micro for i in unresolved_intents)
    if unresolved_intents:
        reasons.append("unresolved_intent_exists")
    if notional <= 0:
        reasons.append("non_positive_notional")
    if notional > settings.max_trade_usdt_micro:
        reasons.append("out_of_policy_trade_size")
    if notional > cap:
        reasons.append("out_of_policy_position_size")
    if reserved + notional > cap:
        reasons.append("aggregate_exposure_cap")

    if candidate.direction is Direction.GRAM_TO_USDT:
        spendable = position.gram_nano - settings.gram_gas_reserve_nano
        if candidate.offer_units > spendable:
            reasons.append("gram_gas_reserve")
    else:
        if candidate.offer_units > position.usdt_micro:
            reasons.append("insufficient_usdt")

    if position.daily_pnl_usdt_micro <= -apply_bps(settings.funding_cap_usdt_micro, settings.daily_loss_bps):
        reasons.append("daily_loss_limit")
    drawdown = position.peak_equity_usdt_micro - position.equity_usdt_micro
    if drawdown >= apply_bps(settings.funding_cap_usdt_micro, settings.drawdown_bps):
        reasons.append("drawdown_limit")

    all_in_bps = quote.fee_percent_bps + quote.price_impact_bps + settings.conservative_slippage_bps
    if all_in_bps > settings.max_all_in_cost_bps:
        reasons.append("all_in_cost")

    verdict = RiskVerdict.REJECT if reasons else RiskVerdict.APPROVE
    return RiskDecision(
        decision_id=decision_id,
        candidate_id=candidate.candidate_id,
        quote_id=quote.quote_id,
        policy_version=settings.policy_version,
        verdict=verdict,
        reasons=reasons,
        reserved_usdt_micro=notional if verdict is RiskVerdict.APPROVE else 0,
        created_at_utc=format_utc(clock.now()),
    )


def should_latch_halt(decision: RiskDecision) -> bool:
    sticky = {"daily_loss_limit", "drawdown_limit", "scope_verification_failed"}
    return any(any(flag in reason for flag in sticky) for reason in decision.reasons)


__all__ = [
    "evaluate_risk",
    "notional_usdt_micro",
    "quote_age_ms",
    "should_latch_halt",
    "gram_from_usdt",
]
