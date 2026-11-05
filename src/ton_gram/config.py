"""Pinned mainnet scope and owner-configurable paper policy.

Identity is address-based. Official USD₮ master is Tether's TON Jetton from
https://tether.to/en/supported-protocols/ : EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs
STON.fi v2 GRAM/USD₮ pool and router are pinned from api.ston.fi mainnet metadata.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ton_gram.addresses import addresses_equal
from ton_gram.units import GRAM_SCALE, USDT_SCALE

# Official issuer-verified USD₮ Jetton master on TON (Tether supported-protocols).
OFFICIAL_USDT_JETTON_MASTER = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"

# Native GRAM (formerly Toncoin) as represented by STON.fi / TON zero address.
NATIVE_GRAM_ADDRESS = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"

# Highest-liquidity STON.fi v2 ConstantProduct GRAM/USD₮ pool (2026-11-05 live check).
PINNED_POOL_ADDRESS = "EQCGScrZe1xbyWqWDvdI6mzP-GAcAWFv6ZXuaJOuSqemxku4"
PINNED_ROUTER_ADDRESS = "EQCS4UEa5UaJLzOyyKieqQOQ2P9M-7kXpkO5HnP3Bv250cN3"
PINNED_PTON_MASTER = "EQBnGWMCf3-FZZq1W4IWcWiGAc3PHuZ0_H-7sad2oY00o83S"
PINNED_PTON_WALLET = "EQCSIMGBps_qzRG3uPYhON8bucyCtu0mYdL1-u4gSz77IBa3"
PINNED_ROUTER_MAJOR = 2
PINNED_ROUTER_MINOR = 2
PINNED_ROUTER_TYPE = "ConstantProduct"
PINNED_PTON_VERSION = "2.1"
PINNED_NETWORK = "mainnet"

# Documented STON.fi v2 router opcodes. Paper mode never encodes or signs them.
DECLARED_OPCODES = {
    "getter_pool_address": "0x2993ade0",
    "reset_gas": "0x29d22935",
    "reset_pool_gas": "0x66d0dff2",
}

POLICY_VERSION = "paper-1.0.0"
PAPER_WALLET_ID = "paper_wallet_1"

DEFAULT_LISTEN_PORT = 43177


def resolve_listen_port() -> int:
    """TON_GRAM_PORT wins; otherwise honor Railway/platform PORT; else 43177."""
    explicit = os.environ.get("TON_GRAM_PORT")
    if explicit not in (None, ""):
        return int(explicit)
    platform = os.environ.get("PORT")
    if platform not in (None, ""):
        return int(platform)
    return DEFAULT_LISTEN_PORT


LOOKALIKE_TICKERS = frozenset(
    {
        "USDT",
        "USD₮",
        "USDt",
        "usdt",
        "GRAM",
        "TON",
        "Toncoin",
        "jUSDT",
        "oUSDT",
        "USD1",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TON_GRAM_",
        env_file=".env",
        extra="ignore",
    )

    network: str = PINNED_NETWORK
    usdt_jetton_master: str = OFFICIAL_USDT_JETTON_MASTER
    native_gram_address: str = NATIVE_GRAM_ADDRESS
    pool_address: str = PINNED_POOL_ADDRESS
    router_address: str = PINNED_ROUTER_ADDRESS
    pton_master: str = PINNED_PTON_MASTER
    pton_wallet: str = PINNED_PTON_WALLET
    router_major_version: int = PINNED_ROUTER_MAJOR
    router_minor_version: int = PINNED_ROUTER_MINOR
    router_type: str = PINNED_ROUTER_TYPE
    pton_version: str = PINNED_PTON_VERSION

    stonfi_base_url: str = "https://api.ston.fi"
    toncenter_url: str = "https://toncenter.com/api/v2/jsonRPC"
    toncenter_v3_url: str = "https://toncenter.com/api/v3"
    tonapi_base_url: str = "https://tonapi.io"
    enable_toncenter: bool = True

    data_dir: Path = Path("data")
    host: str = "0.0.0.0"
    port: int = Field(default_factory=resolve_listen_port)
    snapshot_bearer_token: str = Field(default="paper-dev-token-change-me")
    drill_token: str = ""

    poll_interval_s: float = 15.0
    http_timeout_s: float = 12.0
    max_quote_age_ms: int = 15_000
    min_liquidity_usd_micro: int = 500_000_000_000  # $500,000 in USDT micro
    gram_gas_reserve_nano: int = 250_000_000  # 0.25 GRAM

    funding_cap_usdt_micro: int = 10_000 * USDT_SCALE
    position_cap_bps: int = 200  # 2%
    daily_loss_bps: int = 100  # 1%
    drawdown_bps: int = 300  # 3%
    max_trade_usdt_micro: int = 50 * USDT_SCALE
    max_all_in_cost_bps: int = 80
    conservative_slippage_bps: int = 50
    delay_stress_s: int = 8
    delay_adverse_bps: int = 5
    network_cost_stress_bps: int = 10_000  # 1x extra on quoted gas (2x total)
    measured_net_cost_p90_nano: int = 0
    measured_latency_p90_s: float = 0.0
    cost_model_source: str = "configured"
    network_cost_stress_margin_bps: int = 2_500  # +25% on measured p90 net
    latency_stress_margin_s: int = 2
    heartbeat_miss_s: float = 45.0
    quote_offer_gram_nano: int = GRAM_SCALE  # 1 GRAM
    quote_offer_usdt_micro: int = 10 * USDT_SCALE  # fallback if GRAM→USD₮ ask is missing
    simulate_slippage_tolerance: str = "0.01"
    mid_spread_max_bps: int = 15
    daily_summary_hour_utc: int = 6  # 08:00 Europe/Vilnius in winter

    strategy_lookback: int = 8
    strategy_entry_deviation_bps: int = 25
    strategy_cooldown_cycles: int = 3
    strategy_trade_usdt_micro: int = 50 * USDT_SCALE

    paper_gram_nano: int = 5_000 * GRAM_SCALE
    paper_usdt_micro: int = 5_000 * USDT_SCALE
    paper_wallet_id: str = PAPER_WALLET_ID
    policy_version: str = POLICY_VERSION
    intent_ttl_s: int = 45
    max_assessment_age_s: int = 600

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_dedupe_s: int = 3600

    @field_validator("network")
    @classmethod
    def network_must_be_mainnet_default(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("daily_summary_hour_utc")
    @classmethod
    def hour_must_be_utc_hour(cls, value: int) -> int:
        hour = int(value)
        if hour < 0 or hour > 23:
            raise ValueError("daily_summary_hour_utc must be 0-23")
        return hour

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paper.sqlite3"

    def pinned_mismatch_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.network != PINNED_NETWORK:
            reasons.append(f"network {self.network} != {PINNED_NETWORK}")
        if not addresses_equal(self.usdt_jetton_master, OFFICIAL_USDT_JETTON_MASTER):
            reasons.append("usdt_jetton_master is not Tether official TON USD₮")
        if not addresses_equal(self.native_gram_address, NATIVE_GRAM_ADDRESS):
            reasons.append("native_gram_address is not native GRAM/TON")
        if not addresses_equal(self.pool_address, PINNED_POOL_ADDRESS):
            reasons.append("pool_address is not the pinned STON.fi v2 pool")
        if not addresses_equal(self.router_address, PINNED_ROUTER_ADDRESS):
            reasons.append("router_address is not the pinned STON.fi v2 router")
        if not addresses_equal(self.pton_master, PINNED_PTON_MASTER):
            reasons.append("pton_master mismatch")
        if not addresses_equal(self.pton_wallet, PINNED_PTON_WALLET):
            reasons.append("pton_wallet mismatch")
        if self.router_major_version != PINNED_ROUTER_MAJOR:
            reasons.append("router major version mismatch")
        return reasons


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
