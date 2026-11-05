from fastapi.testclient import TestClient

from ton_gram.api import create_app
from ton_gram.drill import SCENARIOS, DrillHarness, run_all


def test_drills_pass_in_process(settings, clock, ledger):
    settings.drill_token = "drill-secret"
    harness = DrillHarness(settings, clock, ledger)
    result = run_all(harness)
    assert result["passed"] is True
    assert len(result["results"]) == len(SCENARIOS)
    for item in result["results"]:
        assert item["passed"] is True
        assert item["fill_count"] <= 1
        assert item["duplicate_fills"] is False


def test_drill_http_requires_token(settings, clock, ledger):
    settings.drill_token = ""
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    response = client.post("/v1/drill/bounce")
    assert response.status_code == 404


def test_drill_http_all(settings, clock, ledger):
    settings.drill_token = "drill-secret"
    app = create_app(settings, ledger, clock)
    client = TestClient(app)
    response = client.post("/v1/drill/all", headers={"Authorization": "Bearer drill-secret"})
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["signing"] is False
    assert body["broadcast"] is False
