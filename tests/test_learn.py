from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.clock import FrozenClock
from ton_gram.config import Settings
from ton_gram.learn import (
    PINNED_RULES,
    evaluate_paper_day,
    extract_lessons,
    load_learn,
    recommend_rules,
    run_learn,
)
from ton_gram.models import Direction, HaltReason
from ton_gram.scope import verify_scope
from ton_gram.snapshot import build_snapshot
from tests.conftest import (
    make_quote,
    native_gram_asset,
    official_pool,
    official_router,
    official_usdt_asset,
)


def _quiet_report(**overrides):
    body = {
        "day_utc": "2026-11-05",
        "trades_count": 0,
        "entry_halt": False,
        "halt_reason": None,
        "scope_status": "PASS",
        "signal_skips": {
            "warming": 1,
            "flat": 20,
            "cooldown": 0,
            "spread": 0,
            "emitted": 0,
            "approved": 0,
        },
        "feed_uptime": {"stonfi_quotes": {"uptime_pct": 100.0}},
        "quote_age": {"p90": 40},
        "day_realized_pnl_usdt_micro": 0,
    }
    body.update(overrides)
    return body


def test_quiet_book_does_not_retune_entry_gate():
    evaluation = evaluate_paper_day(_quiet_report())
    assert evaluation["score"] >= 90
    lessons = extract_lessons(evaluation)
    assert any(item["id"] == "quiet_book_is_not_a_bug" for item in lessons)
    update = recommend_rules(evaluation)
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25
    assert update["recommended"]["signing"] is False
    assert update["recommended"]["broadcast"] is False


def test_spread_dominated_day_stays_advisory():
    evaluation = evaluate_paper_day(
        _quiet_report(
            signal_skips={
                "warming": 0,
                "flat": 2,
                "cooldown": 0,
                "spread": 18,
                "emitted": 0,
                "approved": 0,
            }
        )
    )
    assert "spread skips dominate" in " ".join(evaluation["errors"])
    lessons = extract_lessons(evaluation)
    assert any(item["id"] == "spread_gate_too_tight" for item in lessons)
    tight = recommend_rules(evaluation, {**PINNED_RULES, "mid_spread_max_bps": 15})
    assert tight["recommended"]["mid_spread_max_bps"] == 200
    assert tight["apply"] is False
    assert tight["recommended"]["strategy_entry_deviation_bps"] == 25
    already = recommend_rules(evaluation, PINNED_RULES)
    assert already["recommended"]["mid_spread_max_bps"] == 200
    assert already["notes"] == []


def test_hard_halt_is_an_error_not_a_retune():
    evaluation = evaluate_paper_day(
        _quiet_report(entry_halt=True, halt_reason="HARD_HALT")
    )
    assert evaluation["score"] <= 80
    assert any("HARD_HALT" in err for err in evaluation["errors"])
    update = recommend_rules(evaluation)
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25


def test_run_learn_persists_and_is_visible_on_snapshot(
    settings: Settings, clock: FrozenClock, ledger
):
    day = clock.now().strftime("%Y-%m-%d")
    for _ in range(8):
        ledger.bump_signal_skip(day, "flat")
    payload = run_learn(ledger, clock, day)
    assert payload["mode"] == "PAPER"
    assert payload["learning_update"]["apply"] is False
    stored = load_learn(ledger)
    assert stored is not None
    assert stored["evaluation"]["signals"]["flat"] == 8

    scope = verify_scope(
        settings,
        observed_network="mainnet",
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
    quotes = {
        Direction.GRAM_TO_USDT: make_quote(clock, Direction.GRAM_TO_USDT, quote_id="q_g"),
        Direction.USDT_TO_GRAM: make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_u"),
    }
    for quote in quotes.values():
        ledger.save_quote(quote)
    snap = build_snapshot(
        settings=settings,
        clock=clock,
        ledger=ledger,
        scope=scope,
        quotes=quotes,
        mid_usdt_per_gram_micro=1_160_000,
        feed_health={"stonfi_quotes": {"status": "UP", "latency_ms": 40}},
    )
    assert snap["learning"]["day_utc"] == payload["day_utc"]
    assert snap["learning"]["learning_update"]["apply"] is False


def test_learn_http_roundtrip(settings: Settings, clock: FrozenClock, ledger):
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    assert client.get("/v1/learn").status_code == 401
    missing = client.get("/v1/learn", headers={"Authorization": "Bearer test-token"})
    assert missing.status_code == 404
    created = client.post(
        "/v1/learn",
        headers={"Authorization": "Bearer test-token"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["mode"] == "PAPER"
    assert body["learning_update"]["apply"] is False
    latest = client.get("/v1/learn", headers={"Authorization": "Bearer test-token"})
    assert latest.status_code == 200
    assert latest.json()["as_of_utc"] == body["as_of_utc"]


def test_latch_halt_shows_in_learn(settings: Settings, clock: FrozenClock, ledger):
    ledger.latch_halt(HaltReason.HARD_HALT)
    payload = run_learn(ledger, clock)
    assert payload["evaluation"]["signals"]["halt"] is True
    assert payload["evaluation"]["signals"]["halt_reason"] == "HARD_HALT"
    assert payload["learning_update"]["apply"] is False
