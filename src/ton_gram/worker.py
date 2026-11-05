"""Supervised paper worker. Recovers first. Never signs. Never broadcasts."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ton_gram.alerts import TelegramAlerter
from ton_gram.clock import Clock
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.models import Direction, HaltReason, RiskVerdict
from ton_gram.poller import MarketPoller, ProviderError
from ton_gram.risk import evaluate_risk, should_latch_halt
from ton_gram.scope import verify_scope
from ton_gram.snapshot import build_snapshot
from ton_gram.strategy import BaselineStrategy

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
        self.strategy = strategy or BaselineStrategy(settings, clock)
        self.alerter = alerter or TelegramAlerter(settings, ledger)
        self.stop_event = threading.Event()
        self.last_snapshot: dict[str, Any] | None = None
        self.recovered = False

    def close(self) -> None:
        self.poller.close()

    def recover_if_needed(self) -> list[dict[str, Any]]:
        outcomes = self.ledger.recover()
        self.recovered = True
        log.info("recovery complete outcomes=%s halt=%s", outcomes, self.ledger.entry_halt)
        return outcomes

    def cycle(self) -> dict[str, Any]:
        if not self.recovered:
            recovery = self.recover_if_needed()
        else:
            recovery = []

        feed_health: dict[str, Any] = {}
        quotes: dict[Direction, QuoteLike] = {}
        mid = 0
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

            quotes = self.poller.fetch_quotes(inputs["pool"])
            feed_health["stonfi_quotes"] = {
                "status": "UP",
                "latency_ms": max(q.provider_latency_ms for q in quotes.values()),
            }
            for quote in quotes.values():
                self.ledger.save_quote(quote)
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
            log.warning("provider failure: %s", exc)
            feed_health["stonfi_quotes"] = {"status": "DOWN", "latency_ms": None, "error": str(exc)}
            scope = verify_scope(self.settings)
            self.ledger.latch_halt(HaltReason.HARD_HALT)
            self._alert("feed_down", f"Provider failure: {exc}")

        if not scope.passed:
            self.ledger.latch_halt(HaltReason.SCOPE_MISMATCH)
            self._alert("scope_fail", f"Scope failed closed: {scope.status}")

        self.ledger.settle_due_intents(mid or 0)

        candidate = None
        decision = None
        intent = None
        if quotes and scope.passed:
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
                    self.ledger.latch_halt(HaltReason.LIMIT_BREACH)
                    self._alert("limit", f"Risk latched halt: {decision.reasons}")
                if decision.verdict is RiskVerdict.APPROVE:
                    intent = self.ledger.create_paper_intent(candidate, quote, decision)
                    log.info("reserved paper intent %s", intent.intent_id)
                else:
                    log.info("candidate rejected: %s", decision.reasons)

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
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "scope": scope.status,
            "candidate_id": candidate.candidate_id if candidate else None,
            "intent_id": intent.intent_id if intent else None,
            "halt": self.ledger.entry_halt,
            "mid_usdt_per_gram_micro": mid,
        }

    def run_forever(self) -> None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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

    def _alert(self, key: str, text: str) -> None:
        try:
            self.alerter.send(key, text)
        except Exception:  # noqa: BLE001
            log.exception("telegram alert failed")


# typing helper for quotes dict in except path
from ton_gram.models import Quote as QuoteLike  # noqa: E402
