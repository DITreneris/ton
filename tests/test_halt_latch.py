from datetime import timedelta
from unittest.mock import patch

from ton_gram.alerts import TelegramAlerter
from ton_gram.clock import format_utc
from ton_gram.models import HaltReason
from ton_gram.poller import ProviderError
from ton_gram.scope import unavailable_scope
from ton_gram.snapshot import build_snapshot
from ton_gram.worker import PaperWorker
from tests.test_alerts import FakeClient


def test_first_latch_wins_until_rearm(settings, clock, ledger):
    assert ledger.latch_halt(HaltReason.LIMIT_BREACH) is True
    assert ledger.halt_reason == "LIMIT_BREACH"
    assert ledger.latch_halt(HaltReason.HARD_HALT) is False
    assert ledger.halt_reason == "LIMIT_BREACH"
    assert ledger.latch_halt(HaltReason.SCOPE_MISMATCH) is False
    assert ledger.halt_reason == "LIMIT_BREACH"
    day = format_utc(clock.now())[:10]
    events = ledger.halt_events_for_day(day)
    assert any(e["action"] == "latch" and e["reason"] == "LIMIT_BREACH" for e in events)
    assert any(e["action"] == "latch_ignored" and e["reason"] == "HARD_HALT" for e in events)
    ledger.rearm()
    assert ledger.entry_halt is False
    assert ledger.latch_halt(HaltReason.HARD_HALT) is True
    assert ledger.halt_reason == "HARD_HALT"


def test_settlement_unknown_upgrades_limit_breach(settings, clock, ledger):
    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    assert ledger.latch_halt(HaltReason.SETTLEMENT_UNKNOWN) is True
    assert ledger.halt_reason == "SETTLEMENT_UNKNOWN"
    events = ledger.halt_events_for_day(format_utc(clock.now())[:10])
    assert any(e["action"] == "latch_upgrade" and e["reason"] == "SETTLEMENT_UNKNOWN" for e in events)


def test_utc_rollover_clears_daily_pnl_not_drawdown(settings, clock, ledger):
    """+$32 after midnight is mark vs today's SOD. Peak drawdown does not reset."""
    high = 1_200_000
    hole = 1_130_000
    bounce = hole + 6_506
    first = ledger.mark_to_market(high)
    clock.advance(1)
    crashed = ledger.mark_to_market(hole)
    assert crashed.daily_pnl_usdt_micro <= -100_000_000
    hole_drawdown = crashed.peak_equity_usdt_micro - crashed.equity_usdt_micro
    assert hole_drawdown >= 300_000_000
    clock.set(clock.now() + timedelta(days=1))
    sod = ledger.mark_to_market(hole)
    assert sod.daily_pnl_usdt_micro == 0
    clock.advance(1)
    bounced = ledger.mark_to_market(bounce)
    assert bounced.daily_pnl_usdt_micro == 32_530_000
    assert bounced.peak_equity_usdt_micro - bounced.equity_usdt_micro >= 300_000_000
    assert first.peak_equity_usdt_micro == bounced.peak_equity_usdt_micro


def test_worker_restores_reason_when_old_ledger_returns_none(settings, clock, ledger):
    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    worker = PaperWorker(settings, clock, ledger)

    def overwrite(reason):
        ledger.set_control("halt_reason", str(reason))
        return None

    worker.ledger.latch_halt = overwrite  # type: ignore[method-assign]
    assert worker._latch(HaltReason.HARD_HALT) is False
    assert worker.ledger.halt_reason == "LIMIT_BREACH"
    worker.close()


def test_feed_down_does_not_overwrite_limit_breach(settings, clock, ledger):
    settings.telegram_enabled = True
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    client = FakeClient()
    worker = PaperWorker(settings, clock, ledger, alerter=TelegramAlerter(settings, ledger, client=client))
    with patch.object(worker.poller, "fetch_scope_inputs", side_effect=ProviderError("offline")):
        summary = worker.cycle()
    assert worker.ledger.halt_reason == "LIMIT_BREACH"
    assert worker.ledger.entry_halt is True
    assert summary["skip"] == "feed_down"
    assert summary["scope"] == "UNAVAILABLE"
    assert summary["halt_reason"] == "LIMIT_BREACH"
    assert any("HARD_HALT" in t for t in client.texts) is False
    assert any("Provider failure" in t for t in client.texts)
    assert ledger.load_signal_skips(format_utc(clock.now())[:10])["feed_down"] == 1
    worker.close()


def test_halted_reject_cycle_prints_marks(settings, clock, ledger):
    from tests.conftest import make_quote
    from tests.test_telegram_cards import _scope_inputs
    from ton_gram.models import Direction

    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    settings.strategy_lookback = 3
    settings.strategy_entry_deviation_bps = 1
    settings.strategy_cooldown_cycles = 0
    worker = PaperWorker(settings, clock, ledger)
    for i in range(3):
        worker.strategy.observe_mid(
            make_quote(clock, Direction.GRAM_TO_USDT, quote_id=f"g{i}", ask_units=1_160_000),
            make_quote(clock, Direction.USDT_TO_GRAM, quote_id=f"u{i}", ask_units=8_620_000_000),
        )
    shocked = make_quote(clock, Direction.GRAM_TO_USDT, quote_id="gX", ask_units=1_200_000)
    shocked_u = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="uX", ask_units=8_330_000_000)
    with patch.object(worker.poller, "fetch_scope_inputs", return_value=_scope_inputs()):
        with patch.object(
            worker.poller,
            "fetch_quotes",
            return_value={Direction.GRAM_TO_USDT: shocked, Direction.USDT_TO_GRAM: shocked_u},
        ):
            summary = worker.cycle()
    assert summary["candidate_id"] is not None
    assert summary["intent_id"] is None
    assert summary["skip"] == "halted"
    assert summary["reject_reasons"] is not None
    assert "entry_halt" in summary["reject_reasons"]
    assert "daily_pnl_usdt_micro" in summary
    assert "drawdown_usdt_micro" in summary
    assert ledger.load_signal_skips(format_utc(clock.now())[:10])["halted"] == 1
    worker.close()


def test_unavailable_scope_does_not_emit_scope_mismatch(settings, clock, ledger):
    snap = build_snapshot(
        settings=settings,
        clock=clock,
        ledger=ledger,
        scope=unavailable_scope(settings, reason="STON.fi 500"),
        quotes={},
        mid_usdt_per_gram_micro=0,
        feed_health={"stonfi_quotes": {"status": "DOWN", "latency_ms": None}},
    )
    assert "SCOPE_MISMATCH" not in [item["code"] for item in snap["incidents"]]
    assert snap["scope_verification"]["status"] == "UNAVAILABLE"
