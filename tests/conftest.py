from __future__ import annotations

from pathlib import Path

import pytest

from ton_gram.addresses import encode_account
from ton_gram.clock import FrozenClock
from ton_gram.config import (
    NATIVE_GRAM_ADDRESS,
    OFFICIAL_USDT_JETTON_MASTER,
    PINNED_POOL_ADDRESS,
    PINNED_PTON_MASTER,
    PINNED_PTON_WALLET,
    PINNED_ROUTER_ADDRESS,
    Settings,
)
from ton_gram.ledger import Ledger
from ton_gram.models import Direction, Quote


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        port=43177,
        snapshot_bearer_token="test-token",
        enable_toncenter=False,
        poll_interval_s=0.01,
        max_quote_age_ms=15_000,
        min_liquidity_usd_micro=1_000_000,
        strategy_lookback=3,
        strategy_entry_deviation_bps=20,
        strategy_cooldown_cycles=0,
        strategy_trade_usdt_micro=50_000_000,
        max_trade_usdt_micro=50_000_000,
        telegram_enabled=False,
        daily_summary_hour_utc=0,
    )


@pytest.fixture
def ledger(settings: Settings, clock: FrozenClock) -> Ledger:
    return Ledger(settings, clock)


def official_usdt_asset() -> dict:
    return {
        "contract_address": OFFICIAL_USDT_JETTON_MASTER,
        "symbol": "USD₮",
        "decimals": 6,
        "kind": "Jetton",
    }


def native_gram_asset() -> dict:
    return {
        "contract_address": NATIVE_GRAM_ADDRESS,
        "symbol": "GRAM",
        "decimals": 9,
        "kind": "Ton",
    }


def official_pool() -> dict:
    return {
        "address": PINNED_POOL_ADDRESS,
        "router_address": PINNED_ROUTER_ADDRESS,
        "token0_address": OFFICIAL_USDT_JETTON_MASTER,
        "token1_address": NATIVE_GRAM_ADDRESS,
        "reserve0": "2000000000000",
        "reserve1": "1700000000000000",
        "lp_total_supply_usd": "4000000",
        "deprecated": False,
        "tags": ["pool:dex_major_version:2", "pool:liquidity:very_high"],
    }


def official_router() -> dict:
    return {
        "address": PINNED_ROUTER_ADDRESS,
        "major_version": 2,
        "minor_version": 2,
        "router_type": "ConstantProduct",
        "pton_master_address": PINNED_PTON_MASTER,
        "pton_wallet_address": PINNED_PTON_WALLET,
        "pton_version": "2.1",
    }


def make_quote(
    clock: FrozenClock,
    direction: Direction,
    *,
    quote_id: str = "q_test",
    offer_units: int | None = None,
    ask_units: int | None = None,
    age_s: int = 0,
    liquidity_usd_micro: int = 4_000_000_000_000,
    router: str = PINNED_ROUTER_ADDRESS,
    pool: str = PINNED_POOL_ADDRESS,
) -> Quote:
    if direction is Direction.GRAM_TO_USDT:
        offer = offer_units if offer_units is not None else 1_000_000_000
        ask = ask_units if ask_units is not None else 1_160_000
        offer_addr, ask_addr = NATIVE_GRAM_ADDRESS, OFFICIAL_USDT_JETTON_MASTER
    else:
        offer = offer_units if offer_units is not None else 10_000_000
        ask = ask_units if ask_units is not None else 8_620_000_000
        offer_addr, ask_addr = OFFICIAL_USDT_JETTON_MASTER, NATIVE_GRAM_ADDRESS
    observed = clock.now()
    if age_s:
        from datetime import timedelta

        from ton_gram.clock import format_utc

        observed_at = format_utc(observed)
        # age is computed vs clock.now(); caller advances after building if needed
    from ton_gram.clock import format_utc

    return Quote(
        quote_id=quote_id,
        direction=direction,
        observed_at_utc=format_utc(clock.now()),
        offer_address=offer_addr,
        ask_address=ask_addr,
        offer_units=offer,
        ask_units=ask,
        min_ask_units=int(ask * 0.99),
        fee_units=max(1, offer // 1000),
        fee_percent_bps=10,
        price_impact_bps=1,
        gas_estimate_nano=45_000_000,
        forward_gas_nano=185_000_000,
        swap_rate_e9=1_160_000_000,
        router_address=router,
        pool_address=pool,
        pton_master=PINNED_PTON_MASTER,
        pton_wallet=PINNED_PTON_WALLET,
        router_major_version=2,
        liquidity_usd_micro=liquidity_usd_micro,
        reserve_gram_nano=1_700_000_000_000_000,
        reserve_usdt_micro=2_000_000_000_000,
        provider_latency_ms=40,
        raw={
            "offer_address": offer_addr,
            "ask_address": ask_addr,
            "router_address": router,
            "pool_address": pool,
            "router": official_router(),
        },
    )


def lookalike_usdt_address() -> str:
    return encode_account(0, "11" * 32)
