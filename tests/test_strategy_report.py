from ton_gram.clock import FrozenClock
from ton_gram.config import Settings
from ton_gram.models import Direction
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
