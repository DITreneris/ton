from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ton_gram.clock import Clock
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.models import AssessmentRecordStatus, HaltReason
from ton_gram.report import build_daily_report
from ton_gram.validator import validate_monitor_result

STATIC_DIR = Path(__file__).parent / "static"


class MonitorResultIn(BaseModel):
    schema_version: str | None = None
    snapshot_id: str | None = None
    as_of_utc: str | None = None
    status: str | None = None
    reason_code: str | None = None
    evidence_ids: list[str] | None = None
    summary: str | None = None

    model_config = {"extra": "allow"}


def create_app(settings: Settings, ledger: Ledger, clock: Clock) -> FastAPI:
    app = FastAPI(title="TON GRAM paper snapshot", version="1.0.0")
    app.state.settings = settings
    app.state.ledger = ledger
    app.state.clock = clock

    def require_bearer(authorization: str | None = Header(default=None)) -> None:
        token = settings.snapshot_bearer_token
        if not authorization or authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

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
        return {"entry_halt": True, "reason": "OPERATOR"}

    @app.post("/v1/control/rearm")
    def rearm(_: None = Depends(require_bearer)) -> dict[str, Any]:
        ledger.rearm()
        return {"entry_halt": False, "reason": None}

    @app.get("/v1/report/daily")
    def daily(_: None = Depends(require_bearer)) -> dict[str, Any]:
        return build_daily_report(ledger, clock)

    return app
