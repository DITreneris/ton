from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.drill import SCENARIOS, isolated_harness, run_all


def test_drills_pass_in_process(settings, clock, ledger):
    settings.drill_token = "drill-secret"
    harness = isolated_harness(settings, clock)
    result = run_all(harness)
    assert result["passed"] is True
    assert result["namespace"] == "drill"
    assert result["paper_untouched"] is True
    assert len(result["results"]) == len(SCENARIOS)
    for item in result["results"]:
        assert item["passed"] is True
        assert item["fill_count"] <= 1
        assert item["duplicate_fills"] is False
    assert ledger.counts()["fills"] == 0
    assert ledger.entry_halt is False
    harness.ledger.close()


def test_drill_http_requires_token(settings, clock, ledger):
    settings.drill_token = ""
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    response = client.post("/v1/drill/bounce")
    assert response.status_code == 404


def test_drill_http_all_does_not_touch_paper(settings, clock, ledger):
    settings.drill_token = "drill-secret"
    start = ledger.get_position()
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    response = client.post("/v1/drill/all", headers={"Authorization": "Bearer drill-secret"})
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["namespace"] == "drill"
    assert body["paper_untouched"] is True
    assert body["signing"] is False
    assert body["broadcast"] is False
    after = ledger.get_position()
    assert after.gram_nano == start.gram_nano
    assert after.usdt_micro == start.usdt_micro
    assert after.realized_pnl_usdt_micro == start.realized_pnl_usdt_micro
    assert ledger.counts()["fills"] == 0
    assert ledger.entry_halt is False
