"""Monitoring snapshot envelope expected by the Grok Risk Monitor."""

from __future__ import annotations

from typing import Any

from ton_gram.clock import Clock, format_utc, parse_utc
from ton_gram.config import Settings
from ton_gram.ledger import Ledger
from ton_gram.models import Direction, Quote, ScopeVerification, to_plain
from ton_gram.learn import load_learn
from ton_gram.risk import quote_age_ms


def _evidence(
    evidence_id: str,
    kind: str,
    as_of_utc: str,
    provider: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "as_of_utc": as_of_utc,
        "provider": provider,
        "summary": summary[:240],
    }


def build_snapshot(
    *,
    settings: Settings,
    clock: Clock,
    ledger: Ledger,
    scope: ScopeVerification,
    quotes: dict[Direction, Quote],
    mid_usdt_per_gram_micro: int,
    feed_health: dict[str, Any],
    recovery: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    now = clock.now()
    as_of = format_utc(now)
    snapshot_id = ledger.next_id("snap")
    position = ledger.get_position()
    unresolved = ledger.unresolved_intents()
    ages = [quote_age_ms(q, clock) for q in quotes.values()]
    max_age = max(ages) if ages else settings.max_quote_age_ms + 1
    both = Direction.GRAM_TO_USDT in quotes and Direction.USDT_TO_GRAM in quotes
    pool_ok = any(q.liquidity_usd_micro >= settings.min_liquidity_usd_micro for q in quotes.values())
    stale = (not both) or max_age > settings.max_quote_age_ms

    incidents: list[dict[str, Any]] = []
    if not scope.passed and scope.status != "UNAVAILABLE":
        incidents.append(
            {
                "code": "SCOPE_MISMATCH",
                "severity": "INCIDENT",
                "summary": "Scope verification failed closed",
            }
        )
    if ledger.entry_halt:
        incidents.append(
            {
                "code": "HARD_HALT" if ledger.halt_reason in {"HARD_HALT", "OPERATOR"} else "LIMIT_BREACH",
                "severity": "INCIDENT",
                "summary": f"Sticky entry halt ({ledger.halt_reason or 'latched'})",
            }
        )
    for item in recovery or []:
        if item.get("status") == "RECONCILED_UNKNOWN":
            incidents.append(
                {
                    "code": "SETTLEMENT_UNKNOWN",
                    "severity": "INCIDENT",
                    "summary": f"Intent {item.get('intent_id')} lacked quote evidence on recover",
                }
            )

    registry: list[dict[str, Any]] = [
        _evidence(
            f"ev_scope_{snapshot_id}",
            "SCOPE",
            as_of,
            "worker",
            f"Scope {scope.status} network={scope.network} pool pinned",
        )
    ]
    for direction, quote in quotes.items():
        registry.append(
            _evidence(
                f"ev_{quote.quote_id}",
                "QUOTE",
                quote.observed_at_utc,
                quote.provider,
                (
                    f"{direction.value} offer={quote.offer_units} ask={quote.ask_units} "
                    f"impact_bps={quote.price_impact_bps} latency_ms={quote.provider_latency_ms}"
                ),
            )
        )
        registry.append(
            _evidence(
                f"ev_pool_{quote.quote_id}",
                "POOL",
                quote.observed_at_utc,
                quote.provider,
                (
                    f"liq_usd_micro={quote.liquidity_usd_micro} "
                    f"gram_reserve={quote.reserve_gram_nano} usdt_reserve={quote.reserve_usdt_micro}"
                ),
            )
        )
    for name, health in feed_health.items():
        registry.append(
            _evidence(
                f"ev_feed_{name}_{snapshot_id}",
                "FEED",
                as_of,
                name,
                f"status={health.get('status')} latency_ms={health.get('latency_ms')}",
            )
        )
    if unresolved:
        registry.append(
            _evidence(
                f"ev_open_{snapshot_id}",
                "LEDGER",
                as_of,
                "worker",
                f"{len(unresolved)} unresolved paper intent(s)",
            )
        )
    for incident in incidents:
        registry.append(
            _evidence(
                f"ev_inc_{incident['code'].lower()}_{snapshot_id}",
                "INCIDENT",
                as_of,
                "worker",
                incident["summary"],
            )
        )

    coverage = {
        "quotes_both_directions": both,
        "pool_liquidity": pool_ok,
        "scope": scope.passed,
        "required_ok": both and pool_ok and scope.passed and not stale,
    }

    snapshot = {
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "as_of_utc": as_of,
        "policy_version": settings.policy_version,
        "mode": "PAPER",
        "scope_verification": to_plain(scope),
        "freshness": {
            "quote_age_ms": max_age if ages else None,
            "max_quote_age_ms": settings.max_quote_age_ms,
            "stale": stale,
            "as_of_utc": as_of,
        },
        "feed_health": feed_health,
        "cost_model": {
            "source": settings.cost_model_source,
            "delay_stress_s": settings.delay_stress_s,
            "delay_adverse_bps": settings.delay_adverse_bps,
            "measured_net_cost_p90_nano": settings.measured_net_cost_p90_nano,
            "profile": ledger.load_gas_profile() or {},
        },
        "required_coverage": coverage,
        "incidents": incidents,
        "open_execution_state": {
            "unresolved_intents": [
                {
                    "intent_id": i.intent_id,
                    "status": i.status.value,
                    "direction": i.direction.value,
                    "created_at_utc": i.created_at_utc,
                    "expires_at_utc": i.expires_at_utc,
                }
                for i in unresolved
            ],
            "entry_halt": ledger.entry_halt,
            "halt_reason": ledger.halt_reason or None,
            "paper_wallet_id": settings.paper_wallet_id,
            "position": {
                "gram_nano": position.gram_nano,
                "usdt_micro": position.usdt_micro,
                "reserved_usdt_micro": position.reserved_usdt_micro,
                "equity_usdt_micro": position.equity_usdt_micro,
                "daily_pnl_usdt_micro": position.daily_pnl_usdt_micro,
                "realized_pnl_usdt_micro": position.realized_pnl_usdt_micro,
            },
            "mid_usdt_per_gram_micro": mid_usdt_per_gram_micro,
        },
        "evidence_registry": registry,
        "learning": load_learn(ledger),
    }
    # Binding helper used by the validator; not a secret.
    snapshot["evidence_ids"] = [item["evidence_id"] for item in registry]
    ledger.save_snapshot(snapshot)
    return snapshot


def snapshot_age_s(snapshot: dict[str, Any], clock: Clock) -> float:
    return (clock.now() - parse_utc(snapshot["as_of_utc"])).total_seconds()
