from ton_gram.clock import FrozenClock
from ton_gram.config import Settings
from ton_gram.models import AssessmentRecordStatus, Direction
from ton_gram.scope import verify_scope
from ton_gram.snapshot import build_snapshot
from ton_gram.validator import schema_errors, validate_monitor_result
from tests.conftest import (
    make_quote,
    native_gram_asset,
    official_pool,
    official_router,
    official_usdt_asset,
)


def _snapshot(settings, clock, ledger):
    scope = verify_scope(
        settings,
        observed_network="mainnet",
        usdt_asset=official_usdt_asset(),
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=[{"router_address": official_router()["address"], "pool_address": official_pool()["address"], "router": official_router()}],
    )
    quotes = {
        Direction.GRAM_TO_USDT: make_quote(clock, Direction.GRAM_TO_USDT, quote_id="q_g"),
        Direction.USDT_TO_GRAM: make_quote(clock, Direction.USDT_TO_GRAM, quote_id="q_u"),
    }
    for q in quotes.values():
        ledger.save_quote(q)
    return build_snapshot(
        settings=settings,
        clock=clock,
        ledger=ledger,
        scope=scope,
        quotes=quotes,
        mid_usdt_per_gram_micro=1_160_000,
        feed_health={"stonfi_quotes": {"status": "UP", "latency_ms": 40}},
    )


def test_valid_no_flag(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    payload = {
        "schema_version": "1.0",
        "snapshot_id": snap["snapshot_id"],
        "as_of_utc": snap["as_of_utc"],
        "status": "NO_FLAG",
        "reason_code": "NO_MATERIAL_FLAG",
        "evidence_ids": [],
        "summary": "Required feeds are current. No material risk flag in this snapshot.",
    }
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.accepted


def test_valid_incident_with_evidence(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    eid = snap["evidence_ids"][0]
    payload = {
        "schema_version": "1.0",
        "snapshot_id": snap["snapshot_id"],
        "as_of_utc": snap["as_of_utc"],
        "status": "INCIDENT",
        "reason_code": "SCOPE_MISMATCH",
        "evidence_ids": [eid],
        "summary": "Scope verification failed closed on the bound snapshot.",
    }
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.accepted


def test_reject_extra_fields(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    payload = {
        "schema_version": "1.0",
        "snapshot_id": snap["snapshot_id"],
        "as_of_utc": snap["as_of_utc"],
        "status": "NO_FLAG",
        "reason_code": "NO_MATERIAL_FLAG",
        "evidence_ids": [],
        "summary": "ok",
        "confidence": 0.9,
    }
    assert schema_errors(payload)
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.record_status is AssessmentRecordStatus.INVALID_SCHEMA


def test_reject_mismatched_snapshot_id(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    payload = {
        "schema_version": "1.0",
        "snapshot_id": "snap_other",
        "as_of_utc": snap["as_of_utc"],
        "status": "NO_FLAG",
        "reason_code": "NO_MATERIAL_FLAG",
        "evidence_ids": [],
        "summary": "bound incorrectly",
    }
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.record_status is AssessmentRecordStatus.SNAPSHOT_MISMATCH


def test_reject_unknown_evidence(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    payload = {
        "schema_version": "1.0",
        "snapshot_id": snap["snapshot_id"],
        "as_of_utc": snap["as_of_utc"],
        "status": "REVIEW_REQUIRED",
        "reason_code": "UNVERIFIED_LEAD",
        "evidence_ids": ["ev_not_in_registry"],
        "summary": "Lead cites an evidence id the snapshot does not contain.",
    }
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.record_status is AssessmentRecordStatus.EVIDENCE_UNKNOWN


def test_reject_late_result(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    clock.advance(settings.max_assessment_age_s + 5)
    payload = {
        "schema_version": "1.0",
        "snapshot_id": snap["snapshot_id"],
        "as_of_utc": snap["as_of_utc"],
        "status": "NO_FLAG",
        "reason_code": "NO_MATERIAL_FLAG",
        "evidence_ids": [],
        "summary": "Arrived after the assessment window.",
    }
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.record_status is AssessmentRecordStatus.LATE


def test_reject_wrong_status_reason_pair(settings: Settings, clock: FrozenClock, ledger):
    snap = _snapshot(settings, clock, ledger)
    payload = {
        "schema_version": "1.0",
        "snapshot_id": snap["snapshot_id"],
        "as_of_utc": snap["as_of_utc"],
        "status": "NO_FLAG",
        "reason_code": "HARD_HALT",
        "evidence_ids": [],
        "summary": "Illegal combination",
    }
    result = validate_monitor_result(payload, snapshot=snap, settings=settings, clock=clock)
    assert result.record_status is AssessmentRecordStatus.INVALID_SCHEMA


def test_unavailable_assessment_is_recorded(settings: Settings, clock: FrozenClock, ledger):
    ledger.record_assessment(None, AssessmentRecordStatus.UNAVAILABLE, {"error": "model timeout"})
    assert ledger.counts()["assessments"] == 1
