"""Read-only STON.fi mainnet poller. Never signs or broadcasts."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ton_gram.clock import Clock, format_utc
from ton_gram.config import (
    NATIVE_GRAM_ADDRESS,
    OFFICIAL_USDT_JETTON_MASTER,
    PINNED_POOL_ADDRESS,
    Settings,
)
from ton_gram.models import Direction, Quote
from ton_gram.units import USDT_SCALE, non_negative_int, require_int


class ProviderError(RuntimeError):
    pass


def _bps_from_ratio(value: Any) -> int:
    if value is None or value == "":
        return 0
    text = str(value)
    try:
        return int(round(float(text) * 10_000))
    except ValueError as exc:
        raise ProviderError(f"non-numeric ratio {value!r}") from exc


def _int_field(payload: dict[str, Any], *names: str, default: int = 0) -> int:
    for name in names:
        if name in payload and payload[name] is not None:
            return non_negative_int(name, payload[name])
    return default


class StonFiClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=settings.stonfi_base_url,
            timeout=settings.http_timeout_s,
            headers={"Accept": "application/json", "User-Agent": "ton-gram-paper/1.0"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _get(self, path: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.client.get(path)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"GET {path} failed: {exc}") from exc
        payload["_provider_latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    def _post_query(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.client.post(path, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"POST {path} failed: {exc}") from exc
        payload["_provider_latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    def get_asset(self, address: str) -> dict[str, Any]:
        payload = self._get(f"/v1/assets/{address}")
        asset = payload.get("asset") or payload
        asset["_provider_latency_ms"] = payload.get("_provider_latency_ms", 0)
        return asset

    def get_pool(self, address: str | None = None) -> dict[str, Any]:
        payload = self._get(f"/v1/pools/{address or self.settings.pool_address}")
        pool = payload.get("pool") or payload
        pool["_provider_latency_ms"] = payload.get("_provider_latency_ms", 0)
        return pool

    def get_router(self, address: str | None = None) -> dict[str, Any]:
        payload = self._get(f"/v1/routers/{address or self.settings.router_address}")
        router = payload.get("router") or payload
        router["_provider_latency_ms"] = payload.get("_provider_latency_ms", 0)
        return router

    def simulate_swap(self, offer_address: str, ask_address: str, units: int) -> dict[str, Any]:
        params = {
            "offer_address": offer_address,
            "ask_address": ask_address,
            "units": str(units),
            "slippage_tolerance": self.settings.simulate_slippage_tolerance,
            "pool_address": self.settings.pool_address,
        }
        return self._post_query("/v1/swap/simulate", params)


class ToncenterClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=settings.http_timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def masterchain_info(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.client.post(
                self.settings.toncenter_url,
                json={"id": 1, "jsonrpc": "2.0", "method": "getMasterchainInfo", "params": {}},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"toncenter getMasterchainInfo failed: {exc}") from exc
        payload["_provider_latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload


def _reserves(pool: dict[str, Any]) -> tuple[int, int]:
    token0 = str(pool.get("token0_address") or "")
    reserve0 = non_negative_int("reserve0", pool.get("reserve0") or 0)
    reserve1 = non_negative_int("reserve1", pool.get("reserve1") or 0)
    from ton_gram.addresses import is_native_ton

    if is_native_ton(token0):
        return reserve0, reserve1
    return reserve1, reserve0


def _liquidity_usd_micro(pool: dict[str, Any]) -> int:
    raw = pool.get("lp_total_supply_usd")
    if raw is None:
        return 0
    try:
        return int(round(float(raw) * USDT_SCALE))
    except ValueError:
        return 0


def quote_from_simulation(
    *,
    direction: Direction,
    simulation: dict[str, Any],
    pool: dict[str, Any],
    clock: Clock,
    quote_id: str,
) -> Quote:
    router = simulation.get("router") or {}
    gas = simulation.get("gas_params") or {}
    return Quote(
        quote_id=quote_id,
        direction=direction,
        observed_at_utc=format_utc(clock.now()),
        offer_address=str(simulation.get("offer_address") or ""),
        ask_address=str(simulation.get("ask_address") or ""),
        offer_units=require_int("offer_units", simulation.get("offer_units") or 0),
        ask_units=require_int("ask_units", simulation.get("ask_units") or 0),
        min_ask_units=require_int("min_ask_units", simulation.get("min_ask_units") or 0),
        fee_units=require_int("fee_units", simulation.get("fee_units") or 0),
        fee_percent_bps=_bps_from_ratio(simulation.get("fee_percent")),
        price_impact_bps=_bps_from_ratio(simulation.get("price_impact")),
        gas_estimate_nano=_int_field(gas, "estimated_gas_consumption", "gas_budget"),
        forward_gas_nano=_int_field(gas, "forward_gas"),
        swap_rate_e9=int(round(float(simulation.get("swap_rate") or 0) * 1_000_000_000)),
        router_address=str(simulation.get("router_address") or router.get("address") or ""),
        pool_address=str(simulation.get("pool_address") or PINNED_POOL_ADDRESS),
        pton_master=str(router.get("pton_master_address") or ""),
        pton_wallet=str(router.get("pton_wallet_address") or ""),
        router_major_version=int(router.get("major_version") or 0),
        liquidity_usd_micro=_liquidity_usd_micro(pool),
        reserve_gram_nano=_reserves(pool)[0],
        reserve_usdt_micro=_reserves(pool)[1],
        provider_latency_ms=int(simulation.get("_provider_latency_ms") or 0),
        raw={k: v for k, v in simulation.items() if k != "_provider_latency_ms"},
    )


class MarketPoller:
    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        ston: StonFiClient | None = None,
        toncenter: ToncenterClient | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.ston = ston or StonFiClient(settings)
        self.toncenter = toncenter
        if toncenter is None and settings.enable_toncenter:
            self.toncenter = ToncenterClient(settings)

    def close(self) -> None:
        self.ston.close()
        if self.toncenter is not None:
            self.toncenter.close()

    def fetch_scope_inputs(self) -> dict[str, Any]:
        usdt = self.ston.get_asset(OFFICIAL_USDT_JETTON_MASTER)
        native = self.ston.get_asset(NATIVE_GRAM_ADDRESS)
        pool = self.ston.get_pool()
        router = self.ston.get_router()
        observed_network = self.settings.network
        toncenter_payload = None
        if self.toncenter is not None:
            try:
                toncenter_payload = self.toncenter.masterchain_info()
                result = toncenter_payload.get("result") or {}
                # mainnet workchain -1 seqno present is a live mainnet signal
                if result.get("@type") == "blocks.masterchainInfo" or result.get("last"):
                    observed_network = "mainnet"
            except ProviderError:
                toncenter_payload = {"error": "unavailable"}
        return {
            "usdt_asset": usdt,
            "native_asset": native,
            "pool": pool,
            "router": router,
            "observed_network": observed_network,
            "toncenter": toncenter_payload,
        }

    def fetch_quotes(self, pool: dict[str, Any], size_usdt_micro: int | None = None) -> dict[Direction, Quote]:
        stamp = format_utc(self.clock.now()).replace(":", "").replace("-", "")
        gram_to_usdt = self.ston.simulate_swap(
            NATIVE_GRAM_ADDRESS,
            OFFICIAL_USDT_JETTON_MASTER,
            self.settings.quote_offer_gram_nano,
        )
        # Size from last mid (same notional), never from the GRAM ask — that would
        # compare bid to ask and look like a 2x-fee "spread" on a healthy book.
        usdt_offer = int(size_usdt_micro or 0)
        if usdt_offer <= 0:
            usdt_offer = self.settings.quote_offer_usdt_micro
        usdt_to_gram = self.ston.simulate_swap(
            OFFICIAL_USDT_JETTON_MASTER,
            NATIVE_GRAM_ADDRESS,
            usdt_offer,
        )
        return {
            Direction.GRAM_TO_USDT: quote_from_simulation(
                direction=Direction.GRAM_TO_USDT,
                simulation=gram_to_usdt,
                pool=pool,
                clock=self.clock,
                quote_id=f"q_g2u_{stamp}",
            ),
            Direction.USDT_TO_GRAM: quote_from_simulation(
                direction=Direction.USDT_TO_GRAM,
                simulation=usdt_to_gram,
                pool=pool,
                clock=self.clock,
                quote_id=f"q_u2g_{stamp}",
            ),
        }
