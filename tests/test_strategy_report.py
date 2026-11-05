from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.clock import FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.models import Candidate, Direction, RiskDecision, RiskVerdict
from ton_gram.report import build_daily_report
from ton_gram.strategy import BaselineStrategy
from tests.conftest import make_quote


def test_restore_zero_cooldown_stays_cooling(settings: Settings, clock: FrozenClock):
    settings.strategy_lookback = 3
    settings.strategy_cooldown_cycles = 3
    settings.strategy_entry_deviation_bps = 1
    first = BaselineStrategy(settings, clock)
    low = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g0", ask_units=1_160_000)
    low_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u0", ask_units=8_620_000_000)
    for _ in range(3):
        first.observe_mid(low, low_u)
    shocked = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g1", ask_units=1_200_000)
    shocked_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u1", ask_units=8_330_000_000)
    mid = first.observe_mid(shocked, shocked_u)
    assert first.maybe_candidate(shocked, shocked_u, mid) is not None
    assert first.cycles_since_signal == 0
    restored = BaselineStrategy(settings, clock, first.dump_state())
    assert restored.cycles_since_signal == 0
    assert restored.maybe_candidate(shocked, shocked_u, mid) is None


def test_worker_persists_cooldown_after_signal(settings: Settings, clock: FrozenClock, tmp_path):
    from ton_gram.ledger import Ledger
    from ton_gram.worker import PaperWorker

    settings.strategy_lookback = 3
    settings.strategy_cooldown_cycles = 3
    settings.strategy_entry_deviation_bps = 1
    db = tmp_path / "paper.sqlite3"
    ledger = Ledger(settings, clock, db)
    worker = PaperWorker(settings, clock, ledger)
    low = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g0", ask_units=1_160_000)
    low_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u0", ask_units=8_620_000_000)
    for i in range(3):
        worker.strategy.observe_mid(
            make_quote(clock, Direction.GRAM_TO_USDT, quote_id=f"g{i}", ask_units=1_160_000),
            make_quote(clock, Direction.USDT_TO_GRAM, quote_id=f"u{i}", ask_units=8_620_000_000),
        )
    shocked = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="gX", ask_units=1_200_000)
    shocked_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="uX", ask_units=8_330_000_000)
    mid = worker.strategy.observe_mid(shocked, shocked_u)
    cand = worker.strategy.maybe_candidate(shocked, shocked_u, mid)
    assert cand is not None
    worker.ledger.save_strategy_state(worker.strategy.dump_state())
    worker.close()
    ledger.close()
    restarted = Ledger(settings, clock, db)
    again = PaperWorker(settings, clock, restarted)
    assert again.strategy.cycles_since_signal == 0
    assert again.strategy.maybe_candidate(shocked, shocked_u, mid) is None
    again.close()
    restarted.close()


def test_strategy_state_survives_restart(settings: Settings, clock: FrozenClock):
    settings.strategy_lookback = 3
    first = BaselineStrategy(settings, clock)
    stable = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g0", ask_units=1_160_000)
    stable_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u0", ask_units=8_620_000_000)
    for _ in range(3):
        first.observe_mid(stable, stable_u)
    assert first.signal_view(first.mids[-1])["skip"] == "flat"
    restarted = BaselineStrategy(settings, clock, first.dump_state())
    view = restarted.signal_view(restarted.mids[-1])
    assert view["skip"] == "flat"
    assert view["samples"] == 3
    assert list(restarted.mids) == list(first.mids)


def test_signal_view_warming_then_flat(settings: Settings, clock: FrozenClock):
    settings.strategy_lookback = 3
    settings.strategy_entry_deviation_bps = 25
    strategy = BaselineStrategy(settings, clock)
    stable = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g0", ask_units=1_160_000)
    stable_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u0", ask_units=8_620_000_000)
    mid = strategy.observe_mid(stable, stable_u)
    warming = strategy.signal_view(mid)
    assert warming["skip"] == "warming"
    assert warming["deviation_bps"] is None
    for _ in range(2):
        mid = strategy.observe_mid(stable, stable_u)
    flat = strategy.signal_view(mid)
    assert flat["skip"] == "flat"
    assert flat["deviation_bps"] == 0
    assert flat["threshold_bps"] == 25


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
