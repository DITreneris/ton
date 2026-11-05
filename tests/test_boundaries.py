from datetime import timedelta

from ton_gram.clock import FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.models import Candidate, Direction, RiskVerdict
from ton_gram.risk import evaluate_risk
from ton_gram.scope import verify_scope
from tests.conftest import (
    lookalike_usdt_address,
    make_quote,
    native_gram_asset,
    official_pool,
    official_router,
    official_usdt_asset,
)


def _scope(settings: Settings, network: str = "mainnet"):
    return verify_scope(
        settings,
        observed_network=network,
        usdt_asset=official_usdt_asset(),
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=[
            {
                "router_address": official_router()["address"],
                "pool_address": official_pool()["address"],
                "router": official_router(),
            }
        ],
    )


def _candidate(clock: FrozenClock, quote_id: str, offer_units: int = 50_000_000) -> Candidate:
    return Candidate(
        candidate_id="cand_1",
        created_at_utc=format_utc(clock.now()),
        direction=Direction.USDT_TO_GRAM,
        offer_units=offer_units,
        quote_id=quote_id,
        mid_usdt_per_gram_micro=1_160_000,
        sma_usdt_per_gram_micro=1_150_000,
        deviation_bps=-80,
        policy_version="paper-1.0.0",
        reason="test",
    )


def _decide(settings, clock, ledger, quote, candidate, scope):
    return evaluate_risk(
        settings=settings,
        clock=clock,
        candidate=candidate,
        quote=quote,
        position=ledger.get_position(),
        scope=scope,
        entry_halt=ledger.entry_halt,
        unresolved_intents=ledger.unresolved_intents(),
        decision_id="dec_1",
    )


def test_stale_quote_zero_intents(settings: Settings, clock: FrozenClock, ledger: Ledger):
    quote = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_stale")
    clock.advance(30)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), _scope(settings))
    assert decision.verdict is RiskVerdict.REJECT
    assert any("stale_quote" in r for r in decision.reasons)


def test_wrong_network_zero_intents(settings: Settings, clock: FrozenClock, ledger: Ledger):
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(
        settings,
        clock,
        ledger,
        quote,
        _candidate(clock, quote.quote_id),
        _scope(settings, network="testnet"),
    )
    assert decision.verdict is RiskVerdict.REJECT
    assert "scope_verification_failed" in decision.reasons


def test_out_of_policy_size_zero_intents(settings: Settings, clock: FrozenClock, ledger: Ledger):
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    huge = settings.funding_cap_usdt_micro  # 100% of funding, far above 2%
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id, offer_units=huge), _scope(settings))
    assert decision.verdict is RiskVerdict.REJECT
    assert any("out_of_policy" in r for r in decision.reasons)


def test_ticker_lookalike_zero_intents(settings: Settings, clock: FrozenClock, ledger: Ledger):
    fake = official_usdt_asset()
    fake["contract_address"] = lookalike_usdt_address()
    fake["symbol"] = "USDT"
    scope = verify_scope(
        settings,
        observed_network="mainnet",
        usdt_asset=fake,
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=[{"router_address": official_router()["address"], "pool_address": official_pool()["address"], "router": official_router()}],
    )
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), scope)
    assert not scope.passed
    assert decision.verdict is RiskVerdict.REJECT
    assert ledger.unresolved_intents() == []


def test_approve_in_policy_size(settings: Settings, clock: FrozenClock, ledger: Ledger):
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id, 50_000_000), _scope(settings))
    assert decision.verdict is RiskVerdict.APPROVE, decision.reasons
