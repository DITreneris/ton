from pathlib import Path

from ton_gram.clock import FrozenClock, format_utc
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.models import Candidate, Direction, HaltReason, IntentStatus, RiskDecision, RiskVerdict
from tests.conftest import make_quote


def _candidate(clock: FrozenClock, quote_id: str) -> Candidate:
    return Candidate(
        candidate_id="cand_rec",
        created_at_utc=format_utc(clock.now()),
        direction=Direction.USDT_TO_GRAM,
        offer_units=50_000_000,
        quote_id=quote_id,
        mid_usdt_per_gram_micro=1_160_000,
        sma_usdt_per_gram_micro=1_150_000,
        deviation_bps=-80,
        policy_version="paper-1.0.0",
        reason="test",
    )


def _decision(clock: FrozenClock, candidate: Candidate) -> RiskDecision:
    return RiskDecision(
        decision_id="dec_rec",
        candidate_id=candidate.candidate_id,
        quote_id=candidate.quote_id,
        policy_version="paper-1.0.0",
        verdict=RiskVerdict.APPROVE,
        reasons=[],
        reserved_usdt_micro=50_000_000,
        created_at_utc=format_utc(clock.now()),
    )


def test_restart_reconciles_unresolved_intent(settings: Settings, clock: FrozenClock, tmp_path: Path):
    db = tmp_path / "paper.sqlite3"
    ledger = Ledger(settings, clock, db)
    quote = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_rec")
    ledger.save_quote(quote)
    cand = _candidate(clock, quote.quote_id)
    ledger.save_candidate(cand)
    dec = _decision(clock, cand)
    ledger.save_decision(dec)
    intent = ledger.create_paper_intent(cand, quote, dec)
    assert intent.status is IntentStatus.RESERVED
    assert ledger.unresolved_intents()
    ledger.close()

    clock.advance(settings.delay_stress_s + 1)
    restarted = Ledger(settings, clock, db)
    outcomes = restarted.recover()
    assert outcomes
    assert restarted.unresolved_intents() == []
    assert restarted.counts()["fills"] == 1
    assert restarted.get_control("recovered") == "1"
    restarted.close()


def test_duplicate_events_ignored(ledger: Ledger):
    first = ledger.ingest_event("evt_1", "quote", {"n": 1})
    second = ledger.ingest_event("evt_1", "quote", {"n": 2})
    assert first is True
    assert second is False


def test_halt_survives_restart(settings: Settings, clock: FrozenClock, tmp_path: Path):
    db = tmp_path / "halt.sqlite3"
    ledger = Ledger(settings, clock, db)
    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    ledger.close()
    restarted = Ledger(settings, clock, db)
    assert restarted.entry_halt is True
    assert restarted.halt_reason == "LIMIT_BREACH"
    restarted.rearm()
    assert restarted.entry_halt is False
    restarted.close()


def test_one_unresolved_intent_per_wallet(settings: Settings, clock: FrozenClock, ledger: Ledger):
    quote = make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_one")
    ledger.save_quote(quote)
    cand = _candidate(clock, quote.quote_id)
    dec = _decision(clock, cand)
    ledger.create_paper_intent(cand, quote, dec)
    try:
        ledger.create_paper_intent(cand, quote, dec)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
