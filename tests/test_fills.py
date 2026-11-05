from ton_gram.clock import FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.fills import simulate_fill
from ton_gram.models import Direction
from tests.conftest import make_quote


def test_stressed_fill_worse_than_raw(settings: Settings, clock: FrozenClock):
    quote = make_quote(clock, Direction.GRAM_TO_USDT)
    fill = simulate_fill(
        fill_id="f1",
        intent_id="i1",
        quote=quote,
        offer_units=1_000_000_000,
        filled_at_utc=format_utc(clock.now()),
        settings=settings,
        mid_usdt_per_gram_micro=quote.usdt_per_gram_micro,
    )
    assert fill.filled_ask_units < fill.raw_ask_units
    assert fill.network_cost_gram_nano > 0
    assert fill.slippage_bps == settings.conservative_slippage_bps
    assert fill.delay_s == settings.delay_stress_s


def test_measured_net_cost_replaces_quote_gas(settings: Settings, clock: FrozenClock):
    settings.measured_net_cost_p90_nano = 8_000_000
    settings.network_cost_stress_margin_bps = 2_500
    quote = make_quote(clock, Direction.GRAM_TO_USDT)
    fill = simulate_fill(
        fill_id="f2",
        intent_id="i2",
        quote=quote,
        offer_units=1_000_000_000,
        filled_at_utc=format_utc(clock.now()),
        settings=settings,
        mid_usdt_per_gram_micro=quote.usdt_per_gram_micro,
    )
    assert fill.network_cost_gram_nano == 8_000_000 + 2_000_000
    assert fill.delay_s == settings.delay_stress_s
