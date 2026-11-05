"""Deterministic mid-deviation baseline. No model overlay. No order authority of its own."""

from __future__ import annotations

from collections import deque
from typing import Any

from ton_gram.clock import Clock, format_utc
from ton_gram.config import Settings
from ton_gram.models import Candidate, Direction, Quote
from ton_gram.units import GRAM_SCALE, USDT_SCALE, bps_delta, gram_from_usdt


class BaselineStrategy:
    """Mean-reversion vs a simple SMA of mid USDT-micro per GRAM."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        state: dict[str, Any] | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.mids: deque[int] = deque(maxlen=max(2, settings.strategy_lookback))
        self.cycles_since_signal = settings.strategy_cooldown_cycles
        self.seq = 0
        self.restore(state)

    def restore(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        for mid in state.get("mids") or []:
            if isinstance(mid, int) and mid > 0:
                self.mids.append(mid)
        cooldown = int(state.get("cycles_since_signal") or self.settings.strategy_cooldown_cycles)
        self.cycles_since_signal = max(0, cooldown)
        self.seq = max(0, int(state.get("seq") or 0))

    def dump_state(self) -> dict[str, Any]:
        return {
            "mids": list(self.mids),
            "cycles_since_signal": self.cycles_since_signal,
            "seq": self.seq,
        }

    def observe_mid(self, gram_to_usdt: Quote, usdt_to_gram: Quote) -> int:
        left = gram_to_usdt.usdt_per_gram_micro
        right = usdt_to_gram.usdt_per_gram_micro
        if left <= 0 or right <= 0:
            return 0
        mid = (left + right) // 2
        self.mids.append(mid)
        self.cycles_since_signal += 1
        return mid

    def signal_view(self, mid: int) -> dict[str, Any]:
        """Why the baseline did or did not emit. Safe to log every cycle."""
        lookback = max(2, self.settings.strategy_lookback)
        threshold = self.settings.strategy_entry_deviation_bps
        samples = len(self.mids)
        if samples < lookback or mid <= 0:
            return {
                "skip": "warming",
                "samples": samples,
                "lookback": lookback,
                "deviation_bps": None,
                "threshold_bps": threshold,
                "sma_usdt_per_gram_micro": None,
            }
        sma = sum(self.mids) // samples
        deviation = bps_delta(mid, sma) if sma > 0 else 0
        if self.cycles_since_signal < self.settings.strategy_cooldown_cycles:
            skip = "cooldown"
        elif sma <= 0 or abs(deviation) < threshold:
            skip = "flat"
        else:
            skip = None
        return {
            "skip": skip,
            "samples": samples,
            "lookback": lookback,
            "deviation_bps": deviation,
            "threshold_bps": threshold,
            "sma_usdt_per_gram_micro": sma,
        }

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
