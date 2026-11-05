"""Simulated paper fills with stressed costs. No chain settlement."""

from __future__ import annotations

from ton_gram.config import Settings
from ton_gram.models import Direction, PaperFill, Quote
from ton_gram.units import apply_bps, reduce_by_bps, usdt_value_micro


def stressed_ask_units(quote: Quote, settings: Settings, offer_units: int) -> tuple[int, int]:
    """Scale the quoted ask to the candidate offer, then apply extra stress."""
    if quote.offer_units <= 0:
        return 0, 0
    raw = (quote.ask_units * offer_units) // quote.offer_units
    stressed = reduce_by_bps(raw, settings.conservative_slippage_bps + settings.delay_adverse_bps)
    return raw, stressed


def network_cost_gram_nano(quote: Quote, settings: Settings) -> int:
    measured = int(getattr(settings, "measured_net_cost_p90_nano", 0) or 0)
    if measured > 0:
        extra = apply_bps(measured, settings.network_cost_stress_margin_bps)
        return measured + extra
    extra = apply_bps(quote.gas_estimate_nano + quote.forward_gas_nano, settings.network_cost_stress_bps)
    return quote.gas_estimate_nano + quote.forward_gas_nano + extra


def simulate_fill(
    *,
    fill_id: str,
    intent_id: str,
    quote: Quote,
    offer_units: int,
    filled_at_utc: str,
    settings: Settings,
    mid_usdt_per_gram_micro: int,
) -> PaperFill:
    raw_ask, filled_ask = stressed_ask_units(quote, settings, offer_units)
    gas_gram = network_cost_gram_nano(quote, settings)
    mid = mid_usdt_per_gram_micro or quote.usdt_per_gram_micro
    gas_usdt = usdt_value_micro(gas_gram, mid)
    if quote.direction is Direction.GRAM_TO_USDT:
        notional = usdt_value_micro(offer_units, mid) if mid > 0 else raw_ask
        received = filled_ask
        shortfall = max(0, notional - received) + gas_usdt
        all_in = (shortfall * 10_000) // notional if notional else 0
        pool_fee = (quote.fee_units * offer_units) // quote.offer_units if quote.offer_units else 0
    else:
        notional = offer_units
        received_usdt = usdt_value_micro(filled_ask, mid)
        shortfall = max(0, notional - received_usdt) + gas_usdt
        all_in = (shortfall * 10_000) // notional if notional else 0
        pool_fee = (quote.fee_units * offer_units) // quote.offer_units if quote.offer_units else 0

    return PaperFill(
        fill_id=fill_id,
        intent_id=intent_id,
        quote_id=quote.quote_id,
        filled_at_utc=filled_at_utc,
        offer_units=offer_units,
        raw_ask_units=raw_ask,
        filled_ask_units=filled_ask,
        pool_fee_units=pool_fee,
        price_impact_bps=quote.price_impact_bps,
        slippage_bps=settings.conservative_slippage_bps,
        delay_s=settings.delay_stress_s,
        delay_adverse_bps=settings.delay_adverse_bps,
        network_cost_gram_nano=gas_gram,
        network_cost_usdt_micro=gas_usdt,
        all_in_cost_bps=all_in,
        mid_usdt_per_gram_micro=mid,
    )


def apply_fill_to_balances(
    *,
    gram_nano: int,
    usdt_micro: int,
    direction: Direction,
    offer_units: int,
    fill: PaperFill,
    gas_reserve_nano: int,
) -> tuple[int, int]:
    if direction is Direction.GRAM_TO_USDT:
        gram_nano = gram_nano - offer_units - fill.network_cost_gram_nano
        usdt_micro = usdt_micro + fill.filled_ask_units
    else:
        usdt_micro = usdt_micro - offer_units
        gram_nano = gram_nano + fill.filled_ask_units - fill.network_cost_gram_nano
    if gram_nano < gas_reserve_nano:
        # Costs can consume reserve in a stressed paper fill; keep non-negative.
        gram_nano = max(0, gram_nano)
    return max(0, gram_nano), max(0, usdt_micro)
