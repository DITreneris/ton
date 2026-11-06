"""Supervised paper worker. Recovers first. Never signs. Never broadcasts."""

from __future__ import annotations

import logging
import threading
from typing import Any

from ton_gram.alerts import TelegramAlerter
from ton_gram.clock import Clock, format_utc, parse_utc
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.logutil import configure_logging
from ton_gram.measure import hydrate_cost_model
from ton_gram.models import Direction, HaltReason, RiskVerdict
from ton_gram.poller import MarketPoller, ProviderError
from ton_gram.report import build_daily_report
from ton_gram.risk import evaluate_risk, quote_age_ms, should_latch_halt
from ton_gram.scope import unavailable_scope, verify_scope
from ton_gram.snapshot import build_snapshot
from ton_gram.strategy import BaselineStrategy
from ton_gram.telegram_text import format_daily_card, format_halt_card
from ton_gram.units import bps_delta, usdt_value_micro

log = logging.getLogger("ton_gram.worker")


class PaperWorker:
    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        ledger: Ledger,
        poller: MarketPoller | None = None,
        strategy: BaselineStrategy | None = None,
        alerter: TelegramAlerter | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.ledger = ledger
        self.poller = poller or MarketPoller(settings, clock)
        self.strategy = strategy or BaselineStrategy(settings, clock, ledger.load_strategy_state())
        self.alerter = alerter or TelegramAlerter(settings, ledger)
        self.stop_event = threading.Event()
        self.last_snapshot: dict[str, Any] | None = None
        self.recovered = False
        self._load_cost_profile()
        self._refresh_stale_heartbeat()

    def _load_cost_profile(self) -> None:
        hydrate_cost_model(self.settings, self.ledger)

    def close(self) -> None:
        self.poller.close()

    def recover_if_needed(self) -> list[dict[str, Any]]:
        outcomes = self.ledger.recover()
        self.recovered = True
        log.info("recovery complete outcomes=%s halt=%s", outcomes, self.ledger.entry_halt)
        if self.ledger.halt_reason == HaltReason.SETTLEMENT_UNKNOWN or any(
            item.get("status") == "RECONCILED_UNKNOWN" for item in outcomes
        ):
            self._alert(
                "halt",
                format_halt_card("SETTLEMENT_UNKNOWN", "Reserved intent missing its quote on recover"),
            )
        return outcomes

    def cycle(self) -> dict[str, Any]:
        if not self.recovered:
            recovery = self.recover_if_needed()
        else:
            recovery = []

        feed_health: dict[str, Any] = {}
        quotes: dict[Direction, QuoteLike] = {}
        mid = 0
        mid_g2u = 0
        mid_u2g = 0
        spread_bps = None
        wide_spread = False
        feed_failed = False
        try:
            inputs = self.poller.fetch_scope_inputs()
            feed_health["stonfi_metadata"] = {
                "status": "UP",
                "latency_ms": (inputs["pool"] or {}).get("_provider_latency_ms"),
            }
            if inputs.get("toncenter") and not inputs["toncenter"].get("error"):
                feed_health["toncenter"] = {
                    "status": "UP",
                    "latency_ms": inputs["toncenter"].get("_provider_latency_ms"),
                }
            elif self.settings.enable_toncenter:
                feed_health["toncenter"] = {"status": "DEGRADED", "latency_ms": None}

            last_mid = self.strategy.mids[-1] if self.strategy.mids else 0
            size_usdt = usdt_value_micro(self.settings.quote_offer_gram_nano, last_mid) if last_mid else None
            quotes = self.poller.fetch_quotes(inputs["pool"], size_usdt_micro=size_usdt)
            feed_health["stonfi_quotes"] = {
                "status": "UP",
                "latency_ms": max(q.provider_latency_ms for q in quotes.values()),
            }
            for quote in quotes.values():
                self.ledger.save_quote(quote)
            mid_g2u = quotes[Direction.GRAM_TO_USDT].usdt_per_gram_micro
            mid_u2g = quotes[Direction.USDT_TO_GRAM].usdt_per_gram_micro
            spread_bps = abs(bps_delta(mid_g2u, mid_u2g)) if mid_g2u and mid_u2g else None
            wide_spread = spread_bps is not None and spread_bps > self.settings.mid_spread_max_bps
            if wide_spread:
                log.warning(
                    "mid spread too wide g2u=%s u2g=%s spread_bps=%s max=%s; SMA unchanged",
                    mid_g2u,
                    mid_u2g,
                    spread_bps,
                    self.settings.mid_spread_max_bps,
                )
                mid = last_mid
            else:
                mid = self.strategy.observe_mid(
                    quotes[Direction.GRAM_TO_USDT],
                    quotes[Direction.USDT_TO_GRAM],
                )
                self.ledger.mark_to_market(mid)
            scope = verify_scope(
                self.settings,
                observed_network=inputs.get("observed_network"),
                usdt_asset=inputs.get("usdt_asset"),
                native_asset=inputs.get("native_asset"),
                pool=inputs.get("pool"),
                router=inputs.get("router"),
                quotes=[q.raw | {"router_address": q.router_address, "pool_address": q.pool_address, "router_major_version": q.router_major_version} for q in quotes.values()],
            )
        except ProviderError as exc:
            feed_failed = True
            log.warning("provider failure: %s", exc)
            feed_health["stonfi_quotes"] = {"status": "DOWN", "latency_ms": None, "error": str(exc)}
            scope = unavailable_scope(self.settings, reason=str(exc))
            if self._latch(HaltReason.HARD_HALT):
                self._alert("halt", format_halt_card("HARD_HALT", f"Provider failure: {exc}"))
            self._alert("feed_down", f"Provider failure: {exc}")

        if not scope.passed and not feed_failed:
            if self._latch(HaltReason.SCOPE_MISMATCH):
                self._alert("halt", format_halt_card("SCOPE_MISMATCH", f"Scope failed closed: {scope.status}"))
            self._alert("scope_fail", f"Scope failed closed: {scope.status}")
            self._alert("incident", "Scope verification failed closed")

        self.ledger.settle_due_intents(mid or 0)

        candidate = None
        decision = None
        intent = None
        if quotes and scope.passed and not wide_spread:
            candidate = self.strategy.maybe_candidate(
                quotes[Direction.GRAM_TO_USDT],
                quotes[Direction.USDT_TO_GRAM],
                mid,
            )
        if candidate is not None:
            self.ledger.save_candidate(candidate)
            quote = quotes[candidate.direction]
            event_id = f"cand:{candidate.candidate_id}"
            if not self.ledger.ingest_event(event_id, "candidate", {"candidate_id": candidate.candidate_id}):
                log.info("duplicate candidate event %s ignored", event_id)
            else:
                decision = evaluate_risk(
                    settings=self.settings,
                    clock=self.clock,
                    candidate=candidate,
                    quote=quote,
                    position=self.ledger.get_position(),
                    scope=scope,
                    entry_halt=self.ledger.entry_halt,
                    unresolved_intents=self.ledger.unresolved_intents(),
                    decision_id=self.ledger.next_id("dec"),
                )
                self.ledger.save_decision(decision)
                if should_latch_halt(decision):
                    if self._latch(HaltReason.LIMIT_BREACH):
                        self._alert(
                            "halt",
                            format_halt_card("LIMIT_BREACH", f"Risk latched halt: {decision.reasons}"),
                        )
                        self._alert("incident", f"Limit incident: {decision.reasons}")
                if decision.verdict is RiskVerdict.APPROVE:
                    intent = self.ledger.create_paper_intent(candidate, quote, decision)
                    log.info("reserved paper intent %s", intent.intent_id)
                else:
                    log.info("candidate rejected: %s", decision.reasons)

        if mid and not wide_spread:
            self.ledger.save_strategy_state(self.strategy.dump_state())

        snapshot = build_snapshot(
            settings=self.settings,
            clock=self.clock,
            ledger=self.ledger,
            scope=scope,
            quotes=quotes,
            mid_usdt_per_gram_micro=mid,
            feed_health=feed_health,
            recovery=recovery,
        )
        self.last_snapshot = snapshot
        age = max((quote_age_ms(q, self.clock) for q in quotes.values()), default=None)
        self.ledger.record_feed_samples(feed_health, age)
        self._maybe_heartbeat_alert()
        self.ledger.record_heartbeat()
        self._maybe_daily_summary()
        if snapshot.get("incidents"):
            self._alert("incident", f"Snapshot incidents: {[i.get('code') for i in snapshot['incidents']]}")
        signal = self.strategy.signal_view(mid)
        if feed_failed:
            signal["skip"] = "feed_down"
        elif wide_spread:
            signal["skip"] = "spread"
        signal["mid_g2u_usdt_per_gram_micro"] = mid_g2u
        signal["mid_u2g_usdt_per_gram_micro"] = mid_u2g
        signal["mid_spread_bps"] = spread_bps
        if intent is not None:
            skip_bucket = "approved"
        elif candidate is not None:
            skip_bucket = "emitted"
        else:
            skip_bucket = signal["skip"]
        skip = None if candidate is not None else signal["skip"]
        self.ledger.bump_signal_skip(format_utc(self.clock.now())[:10], skip_bucket)
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "scope": scope.status,
            "candidate_id": candidate.candidate_id if candidate else None,
            "intent_id": intent.intent_id if intent else None,
            "halt": self.ledger.entry_halt,
            "halt_reason": self.ledger.halt_reason,
            "mid_usdt_per_gram_micro": mid,
            "skip": skip,
            "samples": signal["samples"],
            "lookback": signal["lookback"],
            "deviation_bps": signal["deviation_bps"],
            "threshold_bps": signal["threshold_bps"],
            "sma_usdt_per_gram_micro": signal["sma_usdt_per_gram_micro"],
            "mid_g2u_usdt_per_gram_micro": mid_g2u,
            "mid_u2g_usdt_per_gram_micro": mid_u2g,
            "mid_spread_bps": spread_bps,
        }

    def run_forever(self) -> None:
        configure_logging()
        log.info("paper worker starting; signing=false broadcast=false")
        self.recover_if_needed()
        while not self.stop_event.is_set():
            try:
                summary = self.cycle()
                log.info("cycle %s", summary)
            except Exception:  # noqa: BLE001
                log.exception("cycle failed")
            self.stop_event.wait(self.settings.poll_interval_s)

    def stop(self) -> None:
        self.stop_event.set()

    def reset_paper(self, reason: str = "OPERATOR") -> dict[str, Any]:
        result = self.ledger.reset_paper(reason=reason)
        self.strategy.reset()
        self.ledger.save_strategy_state(self.strategy.dump_state())
        return result

    def _refresh_stale_heartbeat(self) -> None:
        last = self.ledger.last_heartbeat_utc()
        if not last:
            return
        try:
            age = (self.clock.now() - parse_utc(last)).total_seconds()
        except ValueError:
            return
        if age > self.settings.heartbeat_miss_s:
            log.info("stale heartbeat on boot age=%ss; refreshing without alert", int(age))
            self.ledger.record_heartbeat()

    def _latch(self, reason: HaltReason | str) -> bool:
        """Latch halt. First reason wins. Restores prior if an older ledger overwrote it."""
        already = self.ledger.entry_halt
        prior = self.ledger.halt_reason
        changed = self.ledger.latch_halt(reason)
        if changed is None and already and prior:
            upgrade = str(reason) == HaltReason.SETTLEMENT_UNKNOWN.value and prior != str(reason)
            if not upgrade and self.ledger.halt_reason != prior:
                self.ledger.set_control("halt_reason", prior)
            return (not already) or upgrade
        return bool(changed)

    def _alert(self, key: str, text: str) -> None:
        try:
            self.alerter.send(key, text)
        except Exception:  # noqa: BLE001
            log.exception("telegram alert failed")

    def _maybe_heartbeat_alert(self) -> None:
        last = self.ledger.last_heartbeat_utc()
        if not last:
            return
        try:
            age = (self.clock.now() - parse_utc(last)).total_seconds()
        except ValueError:
            return
        if age > self.settings.heartbeat_miss_s:
            self._alert("missed_heartbeat", f"Heartbeat stale by {int(age)}s")

    def _maybe_daily_summary(self) -> None:
        now = self.clock.now()
        if now.hour < self.settings.daily_summary_hour_utc:
            return
        day = format_utc(now)[:10]
        key = f"daily_summary_sent_{day}"
        if self.ledger.recent_alert(key, 36 * 3600):
            return
        try:
            report = build_daily_report(self.ledger, self.clock, day)
        except Exception:  # noqa: BLE001
            log.exception("daily report failed")
            return
        text = format_daily_card(report, funding_cap_usdt_micro=self.settings.funding_cap_usdt_micro)
        try:
            result = self.alerter.send(key, text)
        except Exception:  # noqa: BLE001
            log.exception("telegram daily summary failed")
            return
        if result.get("skipped") and result.get("reason") == "disabled":
            self.ledger.record_alert(key)



# typing helper for quotes dict in except path
from ton_gram.models import Quote as QuoteLike  # noqa: E402
