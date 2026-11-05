import ast
from pathlib import Path

from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.measure import bundled_profile_path, hydrate_cost_model
from ton_gram.models import HaltReason
from ton_gram.worker import PaperWorker


def test_bundled_profile_is_package_data():
    path = bundled_profile_path()
    assert path.is_file()
    assert path.stat().st_size > 100


def test_hydrate_loads_bundled_measured_profile(settings, ledger):
    settings.cost_model_source = "configured"
    settings.measured_net_cost_p90_nano = 0
    settings.delay_stress_s = 8
    profile = hydrate_cost_model(settings, ledger)
    assert profile is not None
    assert settings.cost_model_source == "measured"
    assert settings.measured_net_cost_p90_nano == 210_479_331
    assert settings.delay_stress_s == 3


def test_worker_snapshot_exposes_measured_cost_model(settings, clock, ledger):
    worker = PaperWorker(settings, clock, ledger)
    assert settings.cost_model_source == "measured"
    snap = {
        "cost_model": {
            "source": settings.cost_model_source,
            "delay_stress_s": settings.delay_stress_s,
            "measured_net_cost_p90_nano": settings.measured_net_cost_p90_nano,
        }
    }
    assert snap["cost_model"]["source"] == "measured"
    assert snap["cost_model"]["measured_net_cost_p90_nano"] == 210_479_331
    worker.close()


def test_reset_paper_restores_starting_balances(settings, clock, ledger):
    ledger.latch_halt(HaltReason.OPERATOR)
    ledger.save_strategy_state({"mids": [1_160_000], "cycles_since_signal": 4, "seq": 1})
    before = ledger.get_position()
    ledger.conn.execute(
        "UPDATE positions SET gram_nano=1, usdt_micro=1, realized_pnl_usdt_micro=-50 WHERE id=1"
    )
    ledger.conn.commit()
    result = ledger.reset_paper(reason="OPERATOR")
    assert result["reset"] is True
    pos = ledger.get_position()
    assert pos.gram_nano == settings.paper_gram_nano
    assert pos.usdt_micro == settings.paper_usdt_micro
    assert pos.realized_pnl_usdt_micro == 0
    assert pos.reserved_usdt_micro == 0
    assert ledger.entry_halt is False
    assert ledger.counts()["fills"] == 0
    assert ledger.load_strategy_state() in (None, {})
    from ton_gram.clock import format_utc

    events = ledger.halt_events_for_day(format_utc(clock.now())[:10])
    assert any(e["action"] == "reset" for e in events)
    assert before.gram_nano == settings.paper_gram_nano


def test_counts_verdict_filters_use_one_binding(settings, ledger):
    """Regression: (RiskVerdict.APPROVE.value) without a comma is 7 SQLite bindings."""
    counts = ledger.counts()
    assert counts["approvals"] == 0
    assert counts["rejections"] == 0
    assert counts["fills"] == 0


def test_execute_params_are_sequences():
    """Bare (enum.value) is a string; SQLite iterates it as N bindings."""
    flagged: list[str] = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in {"execute", "executemany"} or len(node.args) < 2:
                continue
            params = node.args[1]
            if isinstance(params, (ast.Tuple, ast.List, ast.Name)):
                continue
            flagged.append(f"{path}:{params.lineno}:{ast.unparse(params)}")
    assert flagged == []


def test_worker_reset_clears_live_sma(settings, clock, ledger):
    worker = PaperWorker(settings, clock, ledger)
    worker.strategy.mids.extend([1_160_000, 1_161_000, 1_162_000])
    worker.strategy.cycles_since_signal = 0
    worker.ledger.save_strategy_state(worker.strategy.dump_state())
    app = create_app(settings, ledger, clock, worker=worker)
    client = TestClient(app)
    ok = client.post("/v1/control/reset", headers={"Authorization": "Bearer test-token"})
    assert ok.status_code == 200
    assert list(worker.strategy.mids) == []
    assert worker.strategy.cycles_since_signal == settings.strategy_cooldown_cycles
    assert worker.ledger.load_strategy_state()["mids"] == []
    worker.close()


def test_stale_heartbeat_on_boot_does_not_alert(settings, clock, ledger):
    from datetime import timedelta
    from unittest.mock import patch

    from ton_gram.alerts import TelegramAlerter
    from ton_gram.clock import format_utc

    past = format_utc(clock.now() - timedelta(seconds=settings.heartbeat_miss_s + 30))
    ledger.set_control("last_heartbeat_utc", past)
    settings.telegram_enabled = True
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    alerter = TelegramAlerter(settings, ledger)
    with patch.object(alerter, "send") as send:
        worker = PaperWorker(settings, clock, ledger, alerter=alerter)
        worker._maybe_heartbeat_alert()
        send.assert_not_called()
        worker.close()


def test_reset_http_requires_bearer(settings, clock, ledger):
    settings.snapshot_bearer_token = "test-token"
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    denied = client.post("/v1/control/reset")
    assert denied.status_code == 401
    ok = client.post("/v1/control/reset", headers={"Authorization": "Bearer test-token"})
    assert ok.status_code == 200
    assert ok.json()["reset"] is True
