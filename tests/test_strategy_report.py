from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.clock import FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.models import Candidate, Direction, RiskDecision, RiskVerdict
from ton_gram.report import build_daily_report
from ton_gram.strategy import BaselineStrategy
from tests.conftest import make_quote


def test_strategy_emits_after_deviation(settings: Settings, clock: FrozenClock):
    strategy = BaselineStrategy(settings, clock)
    stable = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g1", ask_units=1_160_000)
    stable_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u1", ask_units=8_620_000_000)
    for i in range(settings.strategy_lookback):
        mid = strategy.observe_mid(stable, stable_u)
        assert strategy.maybe_candidate(stable, stable_u, mid) is None

    shocked = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g2", ask_units=1_200_000)
    shocked_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u2", ask_units=8_330_000_000)
    mid = strategy.observe_mid(shocked, shocked_u)
    cand = strategy.maybe_candidate(shocked, shocked_u, mid)
    assert cand is not None
    assert cand.direction is Direction.GRAM_TO_USDT


def test_daily_report(settings: Settings, clock: FrozenClock, ledger):
    report = build_daily_report(ledger, clock)
    assert report["mode"] == "PAPER"
    assert report["candidates"] == 0
    assert report["trades"] == []
    assert report["gross_pnl_usdt_micro"] == 0
    assert report["net_pnl_usdt_micro"] == 0
    assert report["rejected_candidates_by_reason"] == {}
    assert "quote_age" in report
    assert "feed_uptime" in report
    assert "No signatures" in report["notes"][-1]


def test_daily_report_honors_date(settings: Settings, clock: FrozenClock, ledger):
    report = build_daily_report(ledger, clock, "2020-01-01")
    assert report["day_utc"] == "2020-01-01"
    assert report["trades_count"] == 0


def test_first_mark_is_start_of_day(settings: Settings, clock: FrozenClock, ledger):
    position = ledger.mark_to_market(1_163_441)
    assert position.daily_pnl_usdt_micro == 0
    assert position.equity_usdt_micro > 0
    clock.advance(1)
    later = ledger.mark_to_market(1_163_441)
    assert later.daily_pnl_usdt_micro == 0


def _populate_paper_day(ledger, clock: FrozenClock) -> None:
    quote = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_daily")
    ledger.save_quote(quote)
    approve = Candidate(
        candidate_id="cand_daily_ok",
        created_at_utc=format_utc(clock.now()),
        direction=Direction.USDT_TO_GRAM,
        offer_units=50_000_000,
        quote_id=quote.quote_id,
        mid_usdt_per_gram_micro=1_160_000,
        sma_usdt_per_gram_micro=1_150_000,
        deviation_bps=-80,
        policy_version="paper-1.0.0",
        reason="daily-report-test",
    )
    reject = Candidate(
        candidate_id="cand_daily_no",
        created_at_utc=format_utc(clock.now()),
        direction=Direction.USDT_TO_GRAM,
        offer_units=50_000_000,
        quote_id=quote.quote_id,
        mid_usdt_per_gram_micro=1_160_000,
        sma_usdt_per_gram_micro=1_150_000,
        deviation_bps=-10,
        policy_version="paper-1.0.0",
        reason="too-small",
    )
    ledger.save_candidate(approve)
    ledger.save_candidate(reject)
    ledger.save_decision(
        RiskDecision(
            decision_id="dec_daily_ok",
            candidate_id=approve.candidate_id,
            quote_id=quote.quote_id,
            policy_version="paper-1.0.0",
            verdict=RiskVerdict.APPROVE,
            reasons=[],
            reserved_usdt_micro=50_000_000,
            created_at_utc=format_utc(clock.now()),
        )
    )
    ledger.save_decision(
        RiskDecision(
            decision_id="dec_daily_no",
            candidate_id=reject.candidate_id,
            quote_id=quote.quote_id,
            policy_version="paper-1.0.0",
            verdict=RiskVerdict.REJECT,
            reasons=["deviation_bps=too-small"],
            reserved_usdt_micro=0,
            created_at_utc=format_utc(clock.now()),
        )
    )
    intent = ledger.create_paper_intent(
        approve,
        quote,
        RiskDecision(
            decision_id="dec_daily_ok",
            candidate_id=approve.candidate_id,
            quote_id=quote.quote_id,
            policy_version="paper-1.0.0",
            verdict=RiskVerdict.APPROVE,
            reasons=[],
            reserved_usdt_micro=50_000_000,
            created_at_utc=format_utc(clock.now()),
        ),
    )
    clock.advance(ledger.settings.delay_stress_s + 1)
    fills = ledger.settle_due_intents(quote.usdt_per_gram_micro)
    assert fills
    assert intent.intent_id == fills[0].intent_id


def test_daily_report_http_on_populated_ledger(settings: Settings, clock: FrozenClock, ledger):
    """GET /v1/report/daily must not 500 after counts() binds APPROVE/REJECT."""
    _populate_paper_day(ledger, clock)
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    denied = client.get("/v1/report/daily")
    assert denied.status_code == 401
    resp = client.get("/v1/report/daily", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "PAPER"
    assert body["trades_count"] == 1
    assert body["approvals"] == 1
    assert body["rejections"] == 1
    assert body["lifetime_counts"]["approvals"] == 1
    assert body["lifetime_counts"]["rejections"] == 1
    assert body["lifetime_counts"]["fills"] == 1
    assert body["paper_fills"] == 1
    assert body["rejected_candidates_by_reason"]["deviation_bps"] == 1
