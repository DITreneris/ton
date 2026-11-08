from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.clock import FrozenClock
from ton_gram.config import Settings
from ton_gram.clock import format_utc
from ton_gram.learn import (
    PINNED_RULES,
    evaluate_paper_day,
    extract_lessons,
    inventory_mark_dominates,
    load_learn,
    recommend_rules,
    rules_from_settings,
    run_learn,
)
from ton_gram.models import HaltReason, RiskDecision, RiskVerdict
from ton_gram.snapshot import build_snapshot
from tests.conftest import (
    make_quote,
    native_gram_asset,
    official_pool,
    official_router,
    official_usdt_asset,
)
from ton_gram.models import Direction
from ton_gram.scope import verify_scope


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


def test_rules_from_settings_uses_live_env(settings: Settings):
    settings.mid_spread_max_bps = 180
    settings.max_all_in_cost_bps = 80
    settings.strategy_entry_deviation_bps = 40
    rules = rules_from_settings(settings)
    assert rules["mid_spread_max_bps"] == 180
    assert rules["max_all_in_cost_bps"] == 80
    assert rules["strategy_entry_deviation_bps"] == 40
    assert rules["signing"] is False
    update = recommend_rules({"signals": {"cycles": 0, "spread": 0}}, rules)
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25
    assert update["recommended"]["signing"] is False
    assert update["recommended"]["max_all_in_cost_bps"] == 80


def test_daily_loss_limit_is_inventory_mark_lesson():
    evaluation = evaluate_paper_day(
        _quiet_report(
            signal_skips={
                "warming": 0,
                "flat": 80,
                "cooldown": 2,
                "spread": 0,
                "emitted": 1,
                "approved": 1,
            },
            trades_count=1,
            entry_halt=True,
            halt_reason="LIMIT_BREACH",
            rejected_candidates_by_reason={"daily_loss_limit": 1},
            day_realized_pnl_usdt_micro=-80_000,
            day_unrealized_pnl_usdt_micro=-118_000_000,
        )
    )
    assert evaluation["signals"]["daily_loss_rejects"] == 1
    assert evaluation["signals"]["unrealized_usdt_micro"] == -118_000_000
    assert not any("daily_loss_limit" in err for err in evaluation["errors"])
    lessons = extract_lessons(evaluation)
    assert any(item["id"] == "daily_loss_marks_inventory" for item in lessons)
    assert any(item["id"] == "audit_fills_after_costs" for item in lessons)
    assert "~170 bps" in next(
        item["text"] for item in lessons if item["id"] == "daily_loss_marks_inventory"
    )
    update = recommend_rules(evaluation)
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25
    assert update["recommended"]["max_all_in_cost_bps"] == 80


def test_realized_daily_loss_is_not_inventory_mark():
    evaluation = evaluate_paper_day(
        _quiet_report(
            signal_skips={
                "warming": 0,
                "flat": 10,
                "cooldown": 0,
                "spread": 0,
                "emitted": 2,
                "approved": 2,
            },
            trades_count=8,
            entry_halt=True,
            halt_reason="LIMIT_BREACH",
            rejected_candidates_by_reason={"daily_loss_limit": 1},
            day_realized_pnl_usdt_micro=-100_000_000,
            day_unrealized_pnl_usdt_micro=-1_000_000,
        )
    )
    lessons = extract_lessons(evaluation)
    assert all(item["id"] != "daily_loss_marks_inventory" for item in lessons)
    update = recommend_rules(evaluation)
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25


def test_drawdown_limit_breach_is_not_inventory_mark():
    evaluation = evaluate_paper_day(
        _quiet_report(
            entry_halt=True,
            halt_reason="LIMIT_BREACH",
            rejected_candidates_by_reason={"drawdown_limit": 1},
            day_unrealized_pnl_usdt_micro=-200_000_000,
        )
    )
    lessons = extract_lessons(evaluation)
    assert all(item["id"] != "daily_loss_marks_inventory" for item in lessons)


def test_halted_print_is_not_fill_two():
    evaluation = evaluate_paper_day(
        _quiet_report(
            signal_skips={
                "warming": 0,
                "flat": 984,
                "cooldown": 6,
                "spread": 0,
                "emitted": 3,
                "approved": 0,
            },
            trades_count=0,
            entry_halt=True,
            halt_reason="LIMIT_BREACH",
            rejected_candidates_by_reason={"entry_halt": 3, "drawdown_limit": 3},
        )
    )
    lessons = extract_lessons(evaluation)
    assert any(item["id"] == "halted_print_is_not_fill_two" for item in lessons)
    assert all(item["id"] != "quiet_book_is_not_a_bug" for item in lessons)
    update = recommend_rules(evaluation)
    assert update["apply"] is False
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25


def test_inventory_mark_dominates_helper():
    assert inventory_mark_dominates(-80_000, -118_000_000) is True
    assert inventory_mark_dominates(-100_000_000, -1_000_000) is False
    assert inventory_mark_dominates(0, 0) is False
    assert inventory_mark_dominates(-50_000, 10_000) is False


def test_all_in_cost_rejects_are_a_named_lesson():
    evaluation = evaluate_paper_day(
        _quiet_report(
            signal_skips={
                "warming": 0,
                "flat": 0,
                "cooldown": 0,
                "spread": 0,
                "emitted": 4,
                "approved": 0,
            },
            rejected_candidates_by_reason={"all_in_cost": 4},
        )
    )
    assert any("all_in_cost" in err for err in evaluation["errors"])
    lessons = extract_lessons(evaluation)
    assert any(item["id"] == "all_in_cost_gate" for item in lessons)
    update = recommend_rules(evaluation)
    assert update["apply"] is False
    assert update["recommended"]["max_all_in_cost_bps"] == 80
    assert update["recommended"]["strategy_entry_deviation_bps"] == 25


def test_run_learn_records_live_rules(settings: Settings, clock: FrozenClock, ledger):
    settings.max_all_in_cost_bps = 80
    settings.mid_spread_max_bps = 200
    payload = run_learn(ledger, clock, settings=settings)
    assert payload["evaluation"]["rules_used"]["mid_spread_max_bps"] == 200
    assert payload["evaluation"]["rules_used"]["max_all_in_cost_bps"] == 80
    assert payload["evaluation"]["rules_used"]["signing"] is False


def test_reset_clears_learn(settings: Settings, clock: FrozenClock, ledger):
    run_learn(ledger, clock, settings=settings)
    assert load_learn(ledger) is not None
    ledger.reset_paper(reason="OPERATOR")
    assert load_learn(ledger) is None


def test_latch_halt_shows_in_learn(settings: Settings, clock: FrozenClock, ledger):
    ledger.latch_halt(HaltReason.HARD_HALT)
    payload = run_learn(ledger, clock)
    assert payload["evaluation"]["signals"]["halt"] is True
    assert payload["evaluation"]["signals"]["halt_reason"] == "HARD_HALT"
    assert payload["learning_update"]["apply"] is False


def test_run_learn_names_inventory_mark_from_ledger(
    settings: Settings, clock: FrozenClock, ledger
):
    ledger.mark_to_market(1_165_521)
    ledger.mark_to_market(1_141_932)
    position = ledger.get_position()
    assert position.daily_pnl_usdt_micro <= -100_000_000
    ledger.save_decision(
        RiskDecision(
            decision_id="dec_daily_loss",
            candidate_id="cand_daily_loss",
            quote_id="q_daily_loss",
            policy_version=settings.policy_version,
            verdict=RiskVerdict.REJECT,
            reasons=["daily_loss_limit"],
            reserved_usdt_micro=0,
            created_at_utc=format_utc(clock.now()),
        )
    )
    ledger.latch_halt(HaltReason.LIMIT_BREACH)
    day = format_utc(clock.now())[:10]
    ledger.bump_signal_skip(day, "flat")
    ledger.bump_signal_skip(day, "approved")
    ledger.bump_signal_skip(day, "emitted")
    payload = run_learn(ledger, clock, settings=settings)
    ids = [item["id"] for item in payload["lessons"]]
    assert "daily_loss_marks_inventory" in ids
    assert payload["evaluation"]["signals"]["daily_loss_rejects"] == 1
    assert payload["learning_update"]["apply"] is False
    assert payload["learning_update"]["recommended"]["strategy_entry_deviation_bps"] == 25
    assert payload["learning_update"]["recommended"]["max_all_in_cost_bps"] == 80
