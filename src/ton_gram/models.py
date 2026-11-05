from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from typing import Any


class Direction(StrEnum):
    GRAM_TO_USDT = "GRAM_TO_USDT"
    USDT_TO_GRAM = "USDT_TO_GRAM"


class IntentStatus(StrEnum):
    RESERVED = "RESERVED"
    FILLED_PAPER = "FILLED_PAPER"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    RECONCILED_UNKNOWN = "RECONCILED_UNKNOWN"


class RiskVerdict(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class AssessmentRecordStatus(StrEnum):
    VALID = "VALID"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    SNAPSHOT_MISMATCH = "SNAPSHOT_MISMATCH"
    EVIDENCE_UNKNOWN = "EVIDENCE_UNKNOWN"
    LATE = "LATE"
    UNAVAILABLE = "UNAVAILABLE"


class HaltReason(StrEnum):
    OPERATOR = "OPERATOR"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    LIMIT_BREACH = "LIMIT_BREACH"
    SETTLEMENT_UNKNOWN = "SETTLEMENT_UNKNOWN"
    HARD_HALT = "HARD_HALT"


def to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    if isinstance(value, StrEnum):
        return str(value)
    return value


@dataclass(slots=True)
class ScopeCheck:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class ScopeVerification:
    status: str
    network: str
    usdt_jetton_master: str
    usdt_issuer: str
    native_asset: str
    native_gram_address: str
    router: str
    pool: str
    pton_master: str
    pton_wallet: str
    router_major_version: int
    router_minor_version: int
    router_type: str
    pton_version: str
    declared_opcodes: dict[str, str]
    checks: list[ScopeCheck]
    fail_closed: bool = True

    @property
    def passed(self) -> bool:
        return self.status == "PASS" and all(c.passed for c in self.checks)


@dataclass(slots=True)
class Quote:
    quote_id: str
    direction: Direction
    observed_at_utc: str
    offer_address: str
    ask_address: str
    offer_units: int
    ask_units: int
    min_ask_units: int
    fee_units: int
    fee_percent_bps: int
    price_impact_bps: int
    gas_estimate_nano: int
    forward_gas_nano: int
    swap_rate_e9: int
    router_address: str
    pool_address: str
    pton_master: str
    pton_wallet: str
    router_major_version: int
    liquidity_usd_micro: int
    reserve_gram_nano: int
    reserve_usdt_micro: int
    provider_latency_ms: int
    provider: str = "ston.fi"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def usdt_per_gram_micro(self) -> int:
        if self.direction is Direction.GRAM_TO_USDT:
            from ton_gram.units import usdt_per_gram_micro
            return usdt_per_gram_micro(self.offer_units, self.ask_units)
        from ton_gram.units import usdt_per_gram_micro
        return usdt_per_gram_micro(self.ask_units, self.offer_units)


@dataclass(slots=True)
class Candidate:
    candidate_id: str
    created_at_utc: str
    direction: Direction
    offer_units: int
    quote_id: str
    mid_usdt_per_gram_micro: int
    sma_usdt_per_gram_micro: int
    deviation_bps: int
    policy_version: str
    reason: str


@dataclass(slots=True)
class RiskDecision:
    decision_id: str
    candidate_id: str
    quote_id: str
    policy_version: str
    verdict: RiskVerdict
    reasons: list[str]
    reserved_usdt_micro: int
    created_at_utc: str


@dataclass(slots=True)
class Intent:
    intent_id: str
    wallet_id: str
    candidate_id: str
    quote_id: str
    policy_version: str
    decision_id: str
    direction: Direction
    offer_units: int
    reserved_usdt_micro: int
    status: IntentStatus
    created_at_utc: str
    expires_at_utc: str
    terminal_at_utc: str | None = None
    notes: str = ""


@dataclass(slots=True)
class PaperFill:
    fill_id: str
    intent_id: str
    quote_id: str
    filled_at_utc: str
    offer_units: int
    raw_ask_units: int
    filled_ask_units: int
    pool_fee_units: int
    price_impact_bps: int
    slippage_bps: int
    delay_s: int
    delay_adverse_bps: int
    network_cost_gram_nano: int
    network_cost_usdt_micro: int
    all_in_cost_bps: int
    mid_usdt_per_gram_micro: int


@dataclass(slots=True)
class Position:
    wallet_id: str
    as_of_utc: str
    gram_nano: int
    usdt_micro: int
    reserved_usdt_micro: int
    equity_usdt_micro: int
    peak_equity_usdt_micro: int
    daily_pnl_usdt_micro: int
    realized_pnl_usdt_micro: int
    day_utc: str
