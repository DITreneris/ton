from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ton_gram.alerts import TelegramAlerter
from ton_gram.clock import Clock
from ton_gram.config import Settings
from ton_gram.drill import SCENARIOS, isolated_harness, run_all
from ton_gram.learn import load_learn, run_learn
from ton_gram.ledger import Ledger
from ton_gram.measure import hydrate_cost_model
from ton_gram.models import HaltReason
from ton_gram.report import build_daily_report
from ton_gram.telegram_text import format_halt_card
from ton_gram.validator import validate_monitor_result

STATIC_DIR = Path(__file__).parent / "static"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MonitorResultIn(BaseModel):
    schema_version: str | None = None
    snapshot_id: str | None = None
    as_of_utc: str | None = None
    status: str | None = None
    reason_code: str | None = None
    evidence_ids: list[str] | None = None
    summary: str | None = None

    model_config = {"extra": "allow"}


def create_app(
    settings: Settings,
    ledger: Ledger,
    clock: Clock,
    alerter: TelegramAlerter | None = None,
    worker: Any = None,
) -> FastAPI:
    app = FastAPI(title="TON GRAM paper snapshot", version="1.0.0")
    hydrate_cost_model(settings, ledger)
    app.state.settings = settings
    app.state.ledger = ledger
    app.state.clock = clock
    app.state.alerter = alerter or TelegramAlerter(settings, ledger)
    app.state.worker = worker

    def require_bearer(authorization: str | None = Header(default=None)) -> None:
        token = settings.snapshot_bearer_token
        if not authorization or authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    def require_drill(authorization: str | None = Header(default=None)) -> None:
        token = settings.drill_token
        if not token:
            raise HTTPException(status_code=404, detail="drill mode disabled")
        if not authorization or authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="missing or invalid drill token")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        snap = ledger.latest_snapshot()
        return {
            "ok": True,
            "mode": "PAPER",
            "signing": False,
            "broadcast": False,
            "entry_halt": ledger.entry_halt,
            "has_snapshot": snap is not None,
            "last_heartbeat_utc": ledger.last_heartbeat_utc() or None,
            "drill_enabled": bool(settings.drill_token),
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/public/status")
    def public_status() -> dict[str, Any]:
        snap = ledger.latest_snapshot()
        if snap is None:
            return {
                "mode": "PAPER",
                "snapshot_id": None,
                "as_of_utc": None,
                "scope_status": "UNAVAILABLE",
                "stale": True,
                "evidence_ids": [],
            }
        scope = snap.get("scope_verification") or {}
        open_state = snap.get("open_execution_state") or {}
        position = open_state.get("position") or {}
        return {
            "mode": snap.get("mode"),
            "snapshot_id": snap.get("snapshot_id"),
            "as_of_utc": snap.get("as_of_utc"),
            "scope_status": scope.get("status"),
            "stale": (snap.get("freshness") or {}).get("stale"),
            "quote_age_ms": (snap.get("freshness") or {}).get("quote_age_ms"),
            "halt_reason": open_state.get("halt_reason"),
            "open_execution_state": {"entry_halt": open_state.get("entry_halt")},
            "equity_usdt_micro": position.get("equity_usdt_micro"),
            "network": scope.get("network"),
            "pool": scope.get("pool"),
            "router": scope.get("router"),
            "usdt": scope.get("usdt_jetton_master"),
            "native": scope.get("native_gram_address"),
            "required_coverage": snap.get("required_coverage"),
            "cost_model": snap.get("cost_model"),
            "evidence_ids": snap.get("evidence_ids") or [],
        }

    @app.get("/v1/snapshot")
    def latest_snapshot(_: None = Depends(require_bearer)) -> JSONResponse:
        snap = ledger.latest_snapshot()
        if snap is None:
            raise HTTPException(status_code=404, detail="no snapshot yet")
        return JSONResponse(snap)

    @app.get("/v1/snapshot/{snapshot_id}")
    def get_snapshot(snapshot_id: str, _: None = Depends(require_bearer)) -> JSONResponse:
        snap = ledger.get_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(status_code=404, detail="snapshot not found")
        return JSONResponse(snap)

    @app.post("/v1/monitor-result")
    def post_monitor_result(body: dict[str, Any], _: None = Depends(require_bearer)) -> dict[str, Any]:
        snapshot_id = body.get("snapshot_id") if isinstance(body, dict) else None
        snapshot = ledger.get_snapshot(snapshot_id) if isinstance(snapshot_id, str) else ledger.latest_snapshot()
        result = validate_monitor_result(
            body,
            snapshot=snapshot,
            settings=settings,
            clock=clock,
        )
        ledger.record_assessment(snapshot_id if isinstance(snapshot_id, str) else None, result.record_status, body if isinstance(body, dict) else {"raw": str(body)})
        return result.to_dict()

    @app.post("/v1/control/halt")
    def halt(_: None = Depends(require_bearer)) -> dict[str, Any]:
        ledger.latch_halt(HaltReason.OPERATOR)
        app.state.alerter.send("halt", format_halt_card("OPERATOR", "Operator latched sticky entry halt"))
        return {"entry_halt": True, "reason": "OPERATOR"}

    @app.post("/v1/control/rearm")
    def rearm(_: None = Depends(require_bearer)) -> dict[str, Any]:
        ledger.rearm()
        return {"entry_halt": False, "reason": None}

    @app.post("/v1/control/reset")
    def reset_paper(_: None = Depends(require_bearer)) -> dict[str, Any]:
        worker = getattr(app.state, "worker", None)
        if worker is not None:
            result = worker.reset_paper(reason="OPERATOR")
        else:
            result = ledger.reset_paper(reason="OPERATOR")
        app.state.alerter.send("incident", "Operator reset paper ledger to starting balances")
        return result

    @app.get("/v1/report/daily")
    def daily(
        _: None = Depends(require_bearer),
        date: str | None = Query(default=None, description="UTC day YYYY-MM-DD"),
    ) -> dict[str, Any]:
        if date and not DATE_RE.match(date):
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        return build_daily_report(ledger, clock, date)

    @app.post("/v1/learn")
    def learn(
        _: None = Depends(require_bearer),
        date: str | None = Query(default=None, description="UTC day YYYY-MM-DD"),
    ) -> dict[str, Any]:
        if date and not DATE_RE.match(date):
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
        return run_learn(ledger, clock, date)

    @app.get("/v1/learn")
    def learn_latest(_: None = Depends(require_bearer)) -> dict[str, Any]:
        payload = load_learn(ledger)
        if payload is None:
            raise HTTPException(status_code=404, detail="no learn pass yet")
        return payload

    @app.post("/v1/drill/all")
    def drill_all(_: None = Depends(require_drill)) -> dict[str, Any]:
        harness = isolated_harness(settings, clock)
        try:
            return run_all(harness)
        finally:
            harness.ledger.close()

    @app.post("/v1/drill/{scenario}")
    def drill_one(scenario: str, _: None = Depends(require_drill)) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise HTTPException(status_code=404, detail="unknown drill scenario")
        harness = isolated_harness(settings, clock)
        try:
            result = harness.run(scenario)
            return {
                "mode": "PAPER",
                "namespace": "drill",
                "paper_untouched": True,
                "signing": False,
                "broadcast": False,
                **result,
            }
        finally:
            harness.ledger.close()

    return app
