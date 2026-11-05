"""MONITOR_RESULT validator: schema, snapshot binding, evidence membership, lateness."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from ton_gram.clock import Clock, parse_utc
from ton_gram.config import Settings
from ton_gram.models import AssessmentRecordStatus

MONITOR_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "snapshot_id",
        "as_of_utc",
        "status",
        "reason_code",
        "evidence_ids",
        "summary",
    ],
    "properties": {
        "schema_version": {"const": "1.0"},
        "snapshot_id": {"type": ["string", "null"], "minLength": 1},
        "as_of_utc": {
            "type": ["string", "null"],
            "pattern": r".*Z$",
        },
        "status": {
            "enum": ["INCIDENT", "INSUFFICIENT_DATA", "REVIEW_REQUIRED", "NO_FLAG"]
        },
        "reason_code": {
            "enum": [
                "HARD_HALT",
                "SCOPE_MISMATCH",
                "SETTLEMENT_UNKNOWN",
                "LIMIT_BREACH",
                "SNAPSHOT_UNAVAILABLE",
                "SNAPSHOT_STALE",
                "FEED_UNAVAILABLE",
                "EVIDENCE_CONFLICT",
                "VERIFIED_EVENT",
                "UNVERIFIED_LEAD",
                "NO_MATERIAL_FLAG",
            ]
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
            "maxItems": 3,
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    "allOf": [
        {
            "if": {"properties": {"status": {"const": "INCIDENT"}}},
            "then": {
                "properties": {
                    "reason_code": {
                        "enum": [
                            "HARD_HALT",
                            "SCOPE_MISMATCH",
                            "SETTLEMENT_UNKNOWN",
                            "LIMIT_BREACH",
                        ]
                    },
                    "snapshot_id": {"type": "string"},
                    "as_of_utc": {"type": "string"},
                    "evidence_ids": {"minItems": 1},
                },
                "required": ["snapshot_id", "as_of_utc", "evidence_ids"],
            },
        },
        {
            "if": {"properties": {"status": {"const": "INSUFFICIENT_DATA"}}},
            "then": {
                "properties": {
                    "reason_code": {
                        "enum": [
                            "SNAPSHOT_UNAVAILABLE",
                            "SNAPSHOT_STALE",
                            "FEED_UNAVAILABLE",
                            "EVIDENCE_CONFLICT",
                        ]
                    }
                }
            },
        },
        {
            "if": {"properties": {"status": {"const": "REVIEW_REQUIRED"}}},
            "then": {
                "properties": {
                    "reason_code": {"enum": ["VERIFIED_EVENT", "UNVERIFIED_LEAD"]},
                    "snapshot_id": {"type": "string"},
                    "as_of_utc": {"type": "string"},
                    "evidence_ids": {"minItems": 1},
                },
                "required": ["snapshot_id", "as_of_utc", "evidence_ids"],
            },
        },
        {
            "if": {"properties": {"status": {"const": "NO_FLAG"}}},
            "then": {
                "properties": {
                    "reason_code": {"const": "NO_MATERIAL_FLAG"},
                    "snapshot_id": {"type": "string"},
                    "as_of_utc": {"type": "string"},
                },
                "required": ["snapshot_id", "as_of_utc"],
            },
        },
    ],
}

_SCHEMA_VALIDATOR = Draft202012Validator(MONITOR_RESULT_SCHEMA)

ALLOWED_REASON = {
    "INCIDENT": {"HARD_HALT", "SCOPE_MISMATCH", "SETTLEMENT_UNKNOWN", "LIMIT_BREACH"},
    "INSUFFICIENT_DATA": {
        "SNAPSHOT_UNAVAILABLE",
        "SNAPSHOT_STALE",
        "FEED_UNAVAILABLE",
        "EVIDENCE_CONFLICT",
    },
    "REVIEW_REQUIRED": {"VERIFIED_EVENT", "UNVERIFIED_LEAD"},
    "NO_FLAG": {"NO_MATERIAL_FLAG"},
}


class ValidationResult:
    def __init__(
        self,
        record_status: AssessmentRecordStatus,
        errors: list[str],
        payload: dict[str, Any] | None,
    ) -> None:
        self.record_status = record_status
        self.errors = errors
        self.payload = payload
        self.accepted = record_status is AssessmentRecordStatus.VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "record_status": self.record_status.value,
            "errors": self.errors,
            "payload": self.payload,
        }


def schema_errors(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    return [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in _SCHEMA_VALIDATOR.iter_errors(payload)
    ]


def validate_monitor_result(
    payload: Any,
    *,
    snapshot: dict[str, Any] | None,
    settings: Settings,
    clock: Clock,
    received_at_bound: bool = True,
) -> ValidationResult:
    errors = schema_errors(payload)
    if errors:
        return ValidationResult(AssessmentRecordStatus.INVALID_SCHEMA, errors, payload if isinstance(payload, dict) else None)

    assert isinstance(payload, dict)
    status = payload["status"]
    reason = payload["reason_code"]
    if reason not in ALLOWED_REASON.get(status, set()):
        return ValidationResult(
            AssessmentRecordStatus.INVALID_SCHEMA,
            [f"reason_code {reason} is not valid for status {status}"],
            payload,
        )

    if snapshot is None:
        if status == "INSUFFICIENT_DATA" and reason == "SNAPSHOT_UNAVAILABLE":
            return ValidationResult(AssessmentRecordStatus.VALID, [], payload)
        return ValidationResult(
            AssessmentRecordStatus.SNAPSHOT_MISMATCH,
            ["no durable snapshot is bound"],
            payload,
        )

    if payload.get("snapshot_id") not in {snapshot["snapshot_id"], None}:
        return ValidationResult(
            AssessmentRecordStatus.SNAPSHOT_MISMATCH,
            ["snapshot_id does not match the bound snapshot"],
            payload,
        )
    if payload.get("as_of_utc") not in {snapshot["as_of_utc"], None}:
        return ValidationResult(
            AssessmentRecordStatus.SNAPSHOT_MISMATCH,
            ["as_of_utc does not match the bound snapshot"],
            payload,
        )
    if status in {"INCIDENT", "REVIEW_REQUIRED", "NO_FLAG"}:
        if payload.get("snapshot_id") != snapshot["snapshot_id"]:
            return ValidationResult(
                AssessmentRecordStatus.SNAPSHOT_MISMATCH,
                ["status requires the bound snapshot_id verbatim"],
                payload,
            )
        if payload.get("as_of_utc") != snapshot["as_of_utc"]:
            return ValidationResult(
                AssessmentRecordStatus.SNAPSHOT_MISMATCH,
                ["status requires the bound as_of_utc verbatim"],
                payload,
            )

    registry = {
        item["evidence_id"]
        for item in snapshot.get("evidence_registry", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    unknown = [eid for eid in payload.get("evidence_ids") or [] if eid not in registry]
    if unknown:
        return ValidationResult(
            AssessmentRecordStatus.EVIDENCE_UNKNOWN,
            [f"evidence id not in registry: {eid}" for eid in unknown],
            payload,
        )

    if received_at_bound:
        try:
            snap_time = parse_utc(snapshot["as_of_utc"])
        except ValueError as exc:
            return ValidationResult(
                AssessmentRecordStatus.INVALID_SCHEMA,
                [f"bound snapshot as_of_utc is not UTC Z: {exc}"],
                payload,
            )
        age = (clock.now() - snap_time).total_seconds()
        if age > settings.max_assessment_age_s:
            return ValidationResult(
                AssessmentRecordStatus.LATE,
                [f"result is {int(age)}s after snapshot; max {settings.max_assessment_age_s}s"],
                payload,
            )

    return ValidationResult(AssessmentRecordStatus.VALID, [], payload)
