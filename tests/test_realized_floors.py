"""Halt floors are realized clip P&L. Marked 5 000 GRAM is telemetry."""

from datetime import timedelta
from unittest.mock import patch

from ton_gram.clock import FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.fills import simulate_fill
from ton_gram.ledger import Ledger
from ton_gram.learn import PINNED_RULES, evaluate_paper_day, extract_lessons, recommend_rules
from ton_gram.models import Direction, HaltReason, RiskVerdict
from ton_gram.poller import ProviderError
from ton_gram.risk import (
    evaluate_risk,
    quote_gate_all_in_bps,
    realized_drawdown_usdt_micro,
    should_latch_halt,
)
from ton_gram.worker import PaperWorker
from tests.conftest import make_quote
from tests.test_boundaries import _candidate, _decide, _scope
from tests.test_learn import _quiet_report
from tests.test_recovery import _candidate as _fill_candidate
from tests.test_recovery import _decision as _fill_decision


def test_marked_inventory_loss_does_not_latch(settings: Settings, clock: FrozenClock, ledger: Ledger):
    ledger.mark_to_market(1_165_521)
    crashed = ledger.mark_to_market(1_141_932)
    assert crashed.daily_pnl_usdt_micro <= -100_000_000
    assert crashed.day_realized_pnl_usdt_micro == 0
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), _scope(settings))
    assert "daily_loss_limit" not in decision.reasons
    assert should_latch_halt(decision) is False
    assert decision.verdict is RiskVerdict.APPROVE


def test_realized_daily_loss_latches(settings: Settings, clock: FrozenClock, ledger: Ledger):
    position = ledger.get_position()
    position.day_realized_pnl_usdt_micro = -100_000_000
    position.realized_pnl_usdt_micro = -100_000_000
    ledger._write_position(position)
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), _scope(settings))
    assert "daily_loss_limit" in decision.reasons
    assert should_latch_halt(decision) is True
    assert decision.verdict is RiskVerdict.REJECT


def test_marked_drawdown_hole_does_not_latch(settings: Settings, clock: FrozenClock, ledger: Ledger):
    ledger.mark_to_market(1_284_000)
    crashed = ledger.mark_to_market(1_035_200)
    marked = crashed.peak_equity_usdt_micro - crashed.equity_usdt_micro
    assert marked >= 1_200_000_000
    assert realized_drawdown_usdt_micro(crashed, settings) == 0
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), _scope(settings))
    assert "drawdown_limit" not in decision.reasons
    assert should_latch_halt(decision) is False
    assert decision.verdict is RiskVerdict.APPROVE


def test_realized_clip_drawdown_latches(settings: Settings, clock: FrozenClock, ledger: Ledger):
    position = ledger.get_position()
    position.realized_pnl_usdt_micro = -300_000_000
    position.peak_clip_equity_usdt_micro = settings.funding_cap_usdt_micro
    ledger._write_position(position)
    position = ledger.get_position()
    assert realized_drawdown_usdt_micro(position, settings) == 300_000_000
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), _scope(settings))
    assert "drawdown_limit" in decision.reasons
    assert should_latch_halt(decision) is True
    assert decision.verdict is RiskVerdict.REJECT


def test_first_latch_wins_when_stonfi_500_arrives(settings: Settings, clock: FrozenClock, ledger: Ledger):
    position = ledger.get_position()
    position.day_realized_pnl_usdt_micro = -100_000_000
    ledger._write_position(position)
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    decision = evaluate_risk(
        settings=settings,
        clock=clock,
        candidate=_candidate(clock, quote.quote_id),
        quote=quote,
        position=ledger.get_position(),
        scope=_scope(settings),
        entry_halt=False,
        unresolved_intents=[],
        decision_id="dec_realized",
    )
    assert "daily_loss_limit" in decision.reasons
    assert should_latch_halt(decision) is True
    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    worker = PaperWorker(settings, clock, ledger)
    with patch.object(worker.poller, "fetch_scope_inputs", side_effect=ProviderError("STON.fi 500")):
        summary = worker.cycle()
    assert worker.ledger.halt_reason == "LIMIT_BREACH"
    assert summary["halt_reason"] == "LIMIT_BREACH"
    assert summary["skip"] == "feed_down"
    worker.close()


def test_reset_paper_clears_realized_floors(settings: Settings, clock: FrozenClock, ledger: Ledger):
    position = ledger.get_position()
    position.day_realized_pnl_usdt_micro = -40_000_000
    position.realized_pnl_usdt_micro = -40_000_000
    position.peak_clip_equity_usdt_micro = settings.funding_cap_usdt_micro + 10
    ledger._write_position(position)
    ledger.reset_paper(reason="OPERATOR")
    restored = ledger.get_position()
    assert restored.day_realized_pnl_usdt_micro == 0
    assert restored.realized_pnl_usdt_micro == 0
    assert restored.peak_clip_equity_usdt_micro == settings.funding_cap_usdt_micro


def test_utc_day_resets_day_realized_not_peak_clip(settings: Settings, clock: FrozenClock, ledger: Ledger):
    position = ledger.get_position()
    position.day_realized_pnl_usdt_micro = -12_000_000
    position.realized_pnl_usdt_micro = -12_000_000
    position.peak_clip_equity_usdt_micro = settings.funding_cap_usdt_micro
    ledger._write_position(position)
    clock.set(clock.now() + timedelta(days=1))
    rolled = ledger.mark_to_market(1_160_000)
    assert rolled.day_realized_pnl_usdt_micro == 0
    assert rolled.realized_pnl_usdt_micro == -12_000_000
    assert rolled.peak_clip_equity_usdt_micro == settings.funding_cap_usdt_micro


def test_save_fill_ratchets_day_realized_and_peak_clip(
    settings: Settings, clock: FrozenClock, ledger: Ledger
):
    quote = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_clip")
    ledger.save_quote(quote)
    cand = _fill_candidate(clock, quote.quote_id)
    ledger.save_candidate(cand)
    dec = _fill_decision(clock, cand)
    ledger.save_decision(dec)
    intent = ledger.create_paper_intent(cand, quote, dec)
    before = ledger.get_position()
    assert before.day_realized_pnl_usdt_micro == 0
    assert before.peak_clip_equity_usdt_micro == settings.funding_cap_usdt_micro
    fill = simulate_fill(
        fill_id="fill_clip",
        intent_id=intent.intent_id,
        quote=quote,
        offer_units=intent.offer_units,
        filled_at_utc=format_utc(clock.now()),
        settings=settings,
        mid_usdt_per_gram_micro=quote.usdt_per_gram_micro,
    )
    after = ledger.save_fill(fill, intent.direction)
    assert after.day_realized_pnl_usdt_micro == after.realized_pnl_usdt_micro
    clip = settings.funding_cap_usdt_micro + after.realized_pnl_usdt_micro
    assert after.peak_clip_equity_usdt_micro == max(settings.funding_cap_usdt_micro, clip)
    assert fill.all_in_cost_bps > quote_gate_all_in_bps(quote, settings)


def test_gas_is_in_fill_score_not_80_gate(settings: Settings, clock: FrozenClock, ledger: Ledger):
    quote = make_quote(clock, Direction.USDT_TO_GRAM)
    gate = quote_gate_all_in_bps(quote, settings)
    assert gate == quote.fee_percent_bps + quote.price_impact_bps + settings.conservative_slippage_bps
    assert gate <= settings.max_all_in_cost_bps
    fill = simulate_fill(
        fill_id="fill_gas",
        intent_id="int_gas",
        quote=quote,
        offer_units=50_000_000,
        filled_at_utc=format_utc(clock.now()),
        settings=settings,
        mid_usdt_per_gram_micro=quote.usdt_per_gram_micro,
    )
    assert fill.network_cost_usdt_micro > 0
    assert fill.all_in_cost_bps >= gate
    decision = _decide(settings, clock, ledger, quote, _candidate(clock, quote.quote_id), _scope(settings))
    assert "all_in_cost" not in decision.reasons
    assert decision.verdict is RiskVerdict.APPROVE


def test_recommend_rules_keep_25_and_80():
    evaluation = evaluate_paper_day(_quiet_report(trades_count=1, day_realized_pnl_usdt_micro=-1_000_000))
    update = recommend_rules(evaluation, {**PINNED_RULES, "max_all_in_cost_bps": 200})
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25
    assert update["recommended"]["max_all_in_cost_bps"] == 80
    assert PINNED_RULES["strategy_entry_deviation_bps"] == 25
    assert PINNED_RULES["max_all_in_cost_bps"] == 80


def test_learn_names_realized_floors_when_mark_is_the_hole():
    evaluation = evaluate_paper_day(
        _quiet_report(
            entry_halt=True,
            halt_reason="LIMIT_BREACH",
            drawdown_usdt_micro=1_243_000_000,
            realized_drawdown_usdt_micro=0,
            day_realized_pnl_usdt_micro=-80_000,
            day_unrealized_pnl_usdt_micro=-118_000_000,
            rejected_candidates_by_reason={"daily_loss_limit": 1},
            trades_count=1,
        )
    )
    ids = [item["id"] for item in extract_lessons(evaluation)]
    assert "risk_floors_are_realized" in ids
    assert "daily_loss_marks_inventory" in ids
    assert "audit_fills_after_costs" in ids
    text = next(item["text"] for item in extract_lessons(evaluation) if item["id"] == "audit_fills_after_costs")
    assert "80–135" in text or "80-135" in text
    update = recommend_rules(evaluation)
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25
    assert update["recommended"]["max_all_in_cost_bps"] == 80


def test_cycle_prints_realized_floors(settings: Settings, clock: FrozenClock, ledger: Ledger):
    worker = PaperWorker(settings, clock, ledger)
    from tests.test_telegram_cards import _scope_inputs

    with patch.object(worker.poller, "fetch_scope_inputs", return_value=_scope_inputs()):
        with patch.object(
            worker.poller,
            "fetch_quotes",
            return_value={
                Direction.GRAM_TO_USDT: make_quote(clock, Direction.GRAM_TO_USDT, quote_id="g0"),
                Direction.USDT_TO_GRAM: make_quote(clock, Direction.USDT_TO_GRAM, quote_id="u0"),
            },
        ):
            summary = worker.cycle()
    assert "day_realized_usdt_micro" in summary
    assert "realized_drawdown_usdt_micro" in summary
    assert summary["day_realized_usdt_micro"] == 0
    assert summary["realized_drawdown_usdt_micro"] == 0
    worker.close()
