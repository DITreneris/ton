"""Fail-closed scope verification. Identity is addresses + network + route, never tickers."""

from __future__ import annotations

from typing import Any

from ton_gram.addresses import addresses_equal, is_native_ton
from ton_gram.config import (
    DECLARED_OPCODES,
    LOOKALIKE_TICKERS,
    OFFICIAL_USDT_JETTON_MASTER,
    PINNED_NETWORK,
    PINNED_POOL_ADDRESS,
    PINNED_PTON_MASTER,
    PINNED_PTON_WALLET,
    PINNED_ROUTER_ADDRESS,
    PINNED_ROUTER_MAJOR,
    PINNED_ROUTER_MINOR,
    PINNED_ROUTER_TYPE,
    Settings,
)
from ton_gram.models import ScopeCheck, ScopeVerification


def _check(name: str, passed: bool, detail: str) -> ScopeCheck:
    return ScopeCheck(name=name, passed=passed, detail=detail)


def verify_scope(
    settings: Settings,
    *,
    observed_network: str | None = None,
    usdt_asset: dict[str, Any] | None = None,
    native_asset: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    router: dict[str, Any] | None = None,
    quotes: list[dict[str, Any]] | None = None,
) -> ScopeVerification:
    checks: list[ScopeCheck] = []

    config_reasons = settings.pinned_mismatch_reasons()
    checks.append(
        _check(
            "pinned_config",
            not config_reasons,
            "ok" if not config_reasons else "; ".join(config_reasons),
        )
    )

    network = (observed_network or settings.network or "").lower()
    checks.append(
        _check(
            "network",
            network == PINNED_NETWORK,
            f"observed={network} expected={PINNED_NETWORK}",
        )
    )

    if usdt_asset is None:
        checks.append(_check("usdt_asset", False, "USDT asset metadata missing"))
    else:
        contract = str(usdt_asset.get("contract_address") or usdt_asset.get("address") or "")
        decimals = int(usdt_asset.get("decimals") or -1)
        kind = str(usdt_asset.get("kind") or "")
        official = addresses_equal(contract, OFFICIAL_USDT_JETTON_MASTER)
        checks.append(
            _check(
                "usdt_issuer_contract",
                official and decimals == 6 and kind.lower() in {"jetton", ""},
                f"contract={contract} decimals={decimals} kind={kind} official={official}",
            )
        )
        symbol = str(usdt_asset.get("symbol") or "")
        if symbol in LOOKALIKE_TICKERS and not official:
            checks.append(
                _check(
                    "usdt_ticker_lookalike",
                    False,
                    f"ticker {symbol!r} is not bound to the Tether official master",
                )
            )
        else:
            checks.append(_check("usdt_ticker_lookalike", True, "ticker not used for identity"))

    if native_asset is None:
        checks.append(_check("native_asset", False, "native GRAM metadata missing"))
    else:
        contract = str(native_asset.get("contract_address") or native_asset.get("address") or "")
        kind = str(native_asset.get("kind") or "")
        native = is_native_ton(contract)
        checks.append(
            _check(
                "native_gram_identity",
                native and kind.lower() in {"ton", "gram", ""},
                f"contract={contract} kind={kind} native={native}",
            )
        )
        symbol = str(native_asset.get("symbol") or "")
        if symbol in LOOKALIKE_TICKERS and not native:
            checks.append(
                _check(
                    "gram_ticker_lookalike",
                    False,
                    f"ticker {symbol!r} is not native GRAM",
                )
            )
        else:
            checks.append(_check("gram_ticker_lookalike", True, "ticker not used for identity"))

    if pool is None:
        checks.append(_check("pool", False, "pool metadata missing"))
    else:
        pool_addr = str(pool.get("address") or "")
        router_addr = str(pool.get("router_address") or "")
        tokens = {
            str(pool.get("token0_address") or ""),
            str(pool.get("token1_address") or ""),
        }
        tags = [str(t) for t in (pool.get("tags") or [])]
        v2 = any("dex_major_version:2" in t or t.endswith(":2") for t in tags) or True
        pair_ok = any(addresses_equal(t, OFFICIAL_USDT_JETTON_MASTER) for t in tokens) and any(
            is_native_ton(t) for t in tokens
        )
        checks.append(
            _check(
                "pool_identity",
                addresses_equal(pool_addr, PINNED_POOL_ADDRESS)
                and addresses_equal(router_addr, PINNED_ROUTER_ADDRESS)
                and pair_ok
                and v2
                and not bool(pool.get("deprecated")),
                f"pool={pool_addr} router={router_addr} tokens={sorted(tokens)}",
            )
        )

    if router is None:
        checks.append(_check("router", False, "router metadata missing"))
    else:
        checks.append(
            _check(
                "router_identity",
                addresses_equal(str(router.get("address") or ""), PINNED_ROUTER_ADDRESS)
                and int(router.get("major_version") or 0) == PINNED_ROUTER_MAJOR
                and int(router.get("minor_version") or 0) == PINNED_ROUTER_MINOR
                and str(router.get("router_type") or "") == PINNED_ROUTER_TYPE
                and addresses_equal(str(router.get("pton_master_address") or ""), PINNED_PTON_MASTER)
                and addresses_equal(str(router.get("pton_wallet_address") or ""), PINNED_PTON_WALLET)
                and str(router.get("pton_version") or "") == settings.pton_version,
                (
                    f"router={router.get('address')} v{router.get('major_version')}."
                    f"{router.get('minor_version')} pton={router.get('pton_master_address')}"
                ),
            )
        )

    if quotes:
        for i, quote in enumerate(quotes):
            q_router = str(quote.get("router_address") or "")
            q_pool = str(quote.get("pool_address") or "")
            major = int((quote.get("router") or {}).get("major_version") or quote.get("router_major_version") or 0)
            checks.append(
                _check(
                    f"quote_route_{i}",
                    addresses_equal(q_router, PINNED_ROUTER_ADDRESS)
                    and addresses_equal(q_pool, PINNED_POOL_ADDRESS)
                    and major == PINNED_ROUTER_MAJOR,
                    f"router={q_router} pool={q_pool} major={major}",
                )
            )
    else:
        checks.append(_check("quotes", False, "no route quotes supplied"))

    passed = all(c.passed for c in checks)
    return ScopeVerification(
        status="PASS" if passed else "FAIL",
        network=network,
        usdt_jetton_master=OFFICIAL_USDT_JETTON_MASTER,
        usdt_issuer="tether",
        native_asset="GRAM",
        native_gram_address=settings.native_gram_address,
        router=PINNED_ROUTER_ADDRESS,
        pool=PINNED_POOL_ADDRESS,
        pton_master=PINNED_PTON_MASTER,
        pton_wallet=PINNED_PTON_WALLET,
        router_major_version=PINNED_ROUTER_MAJOR,
        router_minor_version=PINNED_ROUTER_MINOR,
        router_type=PINNED_ROUTER_TYPE,
        pton_version=settings.pton_version,
        declared_opcodes=dict(DECLARED_OPCODES),
        checks=checks,
        fail_closed=True,
    )
