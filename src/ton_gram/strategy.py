"""Deterministic mid-deviation baseline. No model overlay. No order authority of its own."""

from __future__ import annotations

from collections import deque

from ton_gram.clock import Clock, format_utc
from ton_gram.config import Settings
from ton_gram.models import Candidate, Direction, Quote
from ton_gram.units import GRAM_SCALE, USDT_SCALE, bps_delta, gram_from_usdt


class BaselineStrategy:
    def __init__(self, settings: Settings, clock: Clock) -> None:
        self.settings = settings
        self.clock = clock
        self.mids: deque[int] = deque(maxlen=max(2, settings.strategy_lookback))
        self.cycles_since_signal = settings.strategy_cooldown_cycles
        self.seq = 0

    def observe_mid(self, gram_to_usdt: Quote, usdt_to_gram: Quote) -> int:
        left = gram_to_usdt.usdt_per_gram_micro
        right = usdt_to_gram.usdt_per_gram_micro
        if left <= 0 or right <= 0:
            return 0
        mid = (left + right) // 2
        self.mids.append(mid)
        self.cycles_since_signal += 1
        return mid

    def maybe_candidate(
        self,
        gram_to_usdt: Quote,
        usdt_to_gram: Quote,
        mid: int,
    ) -> Candidate | None:
        lookback = max(2, self.settings.strategy_lookback)
        if len(self.mids) < lookback or mid <= 0:
            return None
        if self.cycles_since_signal < self.settings.strategy_cooldown_cycles:
            return None
        sma = sum(self.mids) // len(self.mids)
        if sma <= 0:
            return None
        deviation = bps_delta(mid, sma)
        threshold = self.settings.strategy_entry_deviation_bps
        if abs(deviation) < threshold:
            return None

        if deviation > 0:
            direction = Direction.GRAM_TO_USDT
            quote = gram_to_usdt
            offer_units = gram_from_usdt(self.settings.strategy_trade_usdt_micro, mid)
            if offer_units <= 0:
                offer_units = GRAM_SCALE
            reason = f"mid {mid} is {deviation} bps above SMA {sma}; fade GRAM"
        else:
            direction = Direction.USDT_TO_GRAM
            quote = usdt_to_gram
            offer_units = self.settings.strategy_trade_usdt_micro
            if offer_units <= 0:
                offer_units = USDT_SCALE
            reason = f"mid {mid} is {deviation} bps below SMA {sma}; fade USD₮"

        self.seq += 1
        self.cycles_since_signal = 0
        return Candidate(
            candidate_id=f"cand_{format_utc(self.clock.now()).replace(':', '')}_{self.seq}",
            created_at_utc=format_utc(self.clock.now()),
            direction=direction,
            offer_units=offer_units,
            quote_id=quote.quote_id,
            mid_usdt_per_gram_micro=mid,
            sma_usdt_per_gram_micro=sma,
            deviation_bps=deviation,
            policy_version=self.settings.policy_version,
            reason=reason,
        )
