from datetime import datetime, timezone

from ton_gram.poller import MarketPoller
from ton_gram.alerts import TelegramAlerter
from ton_gram.telegram_text import format_daily_card, format_halt_card, format_usd
from tests.conftest import official_pool, official_router
from tests.test_alerts import FakeClient


def test_format_usd_signs():
    assert format_usd(0) == "$0.00"
    assert format_usd(2_400_000) == "+$2.40"
    assert format_usd(-300_000) == "-$0.30"


def test_daily_card_uses_report_fields():
    text = format_daily_card(
        {
            "day_utc": "2026-11-05",
            "entry_halt": False,
            "scope_status": "PASS",
            "trades_count": 0,
            "gross_pnl_usdt_micro": 0,
            "fees_usdt_micro": 0,
            "network_cost_usdt_micro": 0,
            "day_realized_pnl_usdt_micro": 0,
            "day_unrealized_pnl_usdt_micro": -300_000,
            "drawdown_usdt_micro": 40_000_000,
            "candidates": 0,
            "rejected_candidates_by_reason": {},
            "signal_skips": {"flat": 12, "warming": 1, "cooldown": 0, "spread": 0, "fired": 0},
            "feed_uptime": {"stonfi_quotes": {"uptime_pct": 100.0}},
            "quote_age": {"p90": 40},
        },
        funding_cap_usdt_micro=10_000_000_000,
    )
    assert "Daily paper report" in text
    assert "Mode: PAPER" in text
    assert "Status: RUNNING" in text
    assert "Scope: PASS" in text
    assert "Unrealized (mark): -$0.30" in text
    assert "Drawdown: +$40.00 (0.40% of $10000 cap)" in text
    assert "Signal: flat 12 / warming 1 / cooldown 0 / spread 0 / fired 0" in text
    assert "Feed: stonfi_quotes UP 100.0%" in text


def test_halt_card_requires_manual_rearm():
    text = format_halt_card("HARD_HALT", "Provider failure: offline")
    assert text.startswith("ENTRY HALTED")
    assert "Reason: HARD_HALT" in text
    assert "New entries blocked." in text
    assert "POST /v1/control/rearm" in text
    assert "offline" in text


def test_daily_summary_waits_until_vilnius_morning(settings, clock, ledger):
    from ton_gram.worker import PaperWorker

    settings.telegram_enabled = True
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    settings.daily_summary_hour_utc = 6
    clock.set(datetime(2026, 11, 5, 5, 59, tzinfo=timezone.utc))
    client = FakeClient()
    worker = PaperWorker(settings, clock, ledger, alerter=TelegramAlerter(settings, ledger, client=client))
    worker._maybe_daily_summary()
    assert client.calls == 0
    clock.set(datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc))
    worker._maybe_daily_summary()
    assert client.calls == 1
    assert "Daily paper report" in client.last_text
    worker._maybe_daily_summary()
    assert client.calls == 1
    worker.close()


def test_feed_down_sends_halt_card(settings, clock, ledger):
    from unittest.mock import patch

    from ton_gram.poller import ProviderError
    from ton_gram.worker import PaperWorker

    settings.telegram_enabled = True
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    client = FakeClient()
    worker = PaperWorker(settings, clock, ledger, alerter=TelegramAlerter(settings, ledger, client=client))
    with patch.object(worker.poller, "fetch_scope_inputs", side_effect=ProviderError("offline")):
        worker.cycle()
    assert client.calls >= 1
    halt_text = next(t for t in client.texts if "HARD_HALT" in t)
    assert "ENTRY HALTED" in halt_text
    worker.close()


def test_stale_quote_does_not_latch_halt(settings, clock, ledger):
    from ton_gram.models import Candidate, Direction, RiskVerdict
    from ton_gram.risk import evaluate_risk, should_latch_halt
    from ton_gram.scope import verify_scope
    from tests.conftest import make_quote, native_gram_asset, official_pool, official_router, official_usdt_asset

    quote = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_stale")
    clock.advance(20)
    cand = Candidate(
        candidate_id="c1",
        created_at_utc="2026-11-05T00:00:00Z",
        direction=Direction.USDT_TO_GRAM,
        offer_units=50_000_000,
        quote_id=quote.quote_id,
        mid_usdt_per_gram_micro=1_160_000,
        sma_usdt_per_gram_micro=1_150_000,
        deviation_bps=-80,
        policy_version=settings.policy_version,
        reason="test",
    )
    scope = verify_scope(
        settings,
        observed_network="mainnet",
        usdt_asset=official_usdt_asset(),
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=[{"router_address": official_router()["address"], "pool_address": official_pool()["address"], "router": official_router()}],
    )
    decision = evaluate_risk(
        settings=settings,
        clock=clock,
        candidate=cand,
        quote=quote,
        position=ledger.get_position(),
        scope=scope,
        entry_halt=False,
        unresolved_intents=[],
        decision_id="dec1",
    )
    assert decision.verdict is RiskVerdict.REJECT
    assert any("stale_quote" in r for r in decision.reasons)
    assert should_latch_halt(decision) is False


def test_wide_spread_skips_candidate(settings, clock, ledger):
    from unittest.mock import patch

    from ton_gram.models import Direction
    from ton_gram.worker import PaperWorker
    from tests.conftest import make_quote

    settings.mid_spread_max_bps = 15
    settings.strategy_lookback = 3
    settings.strategy_entry_deviation_bps = 1
    worker = PaperWorker(settings, clock, ledger)
    g2u = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g_wide", ask_units=1_160_000)
    u2g = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u_wide", offer_units=10_000_000, ask_units=7_000_000_000)
    with patch.object(worker.poller, "fetch_scope_inputs", return_value=_scope_inputs()):
        with patch.object(worker.poller, "fetch_quotes", return_value={Direction.GRAM_TO_USDT: g2u, Direction.USDT_TO_GRAM: u2g}):
            summary = worker.cycle()
    assert summary["candidate_id"] is None
    assert summary["skip"] == "spread"
    assert summary["mid_spread_bps"] is not None and summary["mid_spread_bps"] > 15
    assert ledger.load_signal_skips(clock.now().strftime("%Y-%m-%d"))["spread"] == 1
    worker.close()


def _scope_inputs() -> dict:
    from tests.conftest import native_gram_asset, official_pool, official_router, official_usdt_asset

    return {
        "usdt_asset": official_usdt_asset(),
        "native_asset": native_gram_asset(),
        "pool": official_pool(),
        "router": official_router(),
        "observed_network": "mainnet",
        "toncenter": None,
    }


class _FakeSton:
    def __init__(self) -> None:
        self.units: list[int] = []

    def close(self) -> None:
        return None

    def simulate_swap(self, offer_address: str, ask_address: str, units: int) -> dict:
        self.units.append(int(units))
        router = official_router()
        if offer_address.endswith("AM9c") or "AM9c" in offer_address:
            ask = 1_160_000
            return {
                "offer_address": offer_address,
                "ask_address": ask_address,
                "offer_units": units,
                "ask_units": ask,
                "min_ask_units": ask,
                "fee_units": 1,
                "fee_percent": "0.001",
                "price_impact": "0.0001",
                "swap_rate": "1.16",
                "router_address": router["address"],
                "pool_address": official_pool()["address"],
                "router": router,
                "gas_params": {"estimated_gas_consumption": 1, "forward_gas": 1},
            }
        ask_gram = (int(units) * 1_000_000_000) // 1_160_000
        return {
            "offer_address": offer_address,
            "ask_address": ask_address,
            "offer_units": units,
            "ask_units": ask_gram,
            "min_ask_units": ask_gram,
            "fee_units": 1,
            "fee_percent": "0.001",
            "price_impact": "0.0001",
            "swap_rate": "1.16",
            "router_address": router["address"],
            "pool_address": official_pool()["address"],
            "router": router,
            "gas_params": {"estimated_gas_consumption": 1, "forward_gas": 1},
        }


def test_fetch_quotes_size_matches_second_leg(settings, clock):
    ston = _FakeSton()
    poller = MarketPoller(settings, clock, ston=ston, toncenter=None)
    quotes = poller.fetch_quotes(official_pool())
    assert ston.units[0] == settings.quote_offer_gram_nano
    assert ston.units[1] == 1_160_000
    assert quotes
    poller.close()
