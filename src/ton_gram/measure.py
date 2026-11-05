"""Read-only gas/refund/latency measurement on the pinned STON.fi v2 pool.

Uses public TonAPI events + traces. Never signs or broadcasts.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ton_gram.addresses import addresses_equal
from ton_gram.clock import format_utc
from ton_gram.config import (
    OFFICIAL_USDT_JETTON_MASTER,
    PINNED_POOL_ADDRESS,
    PINNED_ROUTER_ADDRESS,
    Settings,
)
from ton_gram.models import Direction
from ton_gram.stats import summarize_floats, summarize_ints

log = logging.getLogger("ton_gram.measure")

USDT_RAW = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"


@dataclass
class SwapSample:
    event_id: str
    direction: str
    timestamp_utc: str
    user_wallet: str
    attached_gram_nano: int
    offer_gram_nano: int
    refund_gram_nano: int
    net_cost_gram_nano: int
    latency_s: int
    hops: int
    success: bool
    source: str = "tonapi"


def _utc_from_unix(ts: int) -> str:
    return format_utc(datetime.fromtimestamp(int(ts), tz=timezone.utc))


def _walk_trace(node: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [node]
    for child in node.get("children") or []:
        rows.extend(_walk_trace(child))
    return rows


def classify_swap(action: dict[str, Any]) -> tuple[str, int] | None:
    if action.get("type") != "JettonSwap" or action.get("status") != "ok":
        return None
    body = action.get("JettonSwap") or {}
    if str(body.get("dex") or "").lower() != "stonfi":
        return None
    router = str((body.get("router") or {}).get("address") or "")
    if router and not addresses_equal(router, PINNED_ROUTER_ADDRESS):
        return None
    ton_in = int(body.get("ton_in") or 0)
    ton_out = int(body.get("ton_out") or 0)
    jetton_in = str((body.get("jetton_master_in") or {}).get("address") or "")
    jetton_out = str((body.get("jetton_master_out") or {}).get("address") or "")
    if ton_in > 0 and (addresses_equal(jetton_out, OFFICIAL_USDT_JETTON_MASTER) or jetton_out.lower() == USDT_RAW):
        return Direction.GRAM_TO_USDT.value, ton_in
    if ton_out > 0 and (addresses_equal(jetton_in, OFFICIAL_USDT_JETTON_MASTER) or jetton_in.lower() == USDT_RAW):
        return Direction.USDT_TO_GRAM.value, 0
    return None


def extract_costs(trace: dict[str, Any], user_wallet: str, offer_gram_nano: int) -> dict[str, int]:
    """Attached GRAM is the first outbound from the user wallet (gas + native offer)."""
    refund = 0
    utimes: list[int] = []
    hops = 0
    attached = 0
    first_out = (trace.get("children") or [{}])[0]
    first_in = ((first_out.get("transaction") or {}).get("in_msg") or {})
    first_op = str(first_in.get("decoded_op_name") or "")
    if first_op in {"pton_ton_transfer", "jetton_transfer", "jetton_notify"}:
        attached = int(first_in.get("value") or 0)
    nodes = _walk_trace(trace)
    for node in nodes:
        tx = node.get("transaction") or {}
        hops += 1
        if tx.get("utime"):
            utimes.append(int(tx["utime"]))
        in_msg = tx.get("in_msg") or {}
        op = str(in_msg.get("decoded_op_name") or "")
        value = int(in_msg.get("value") or 0)
        dest = str((in_msg.get("destination") or {}).get("address") or "")
        if attached == 0 and op == "pton_ton_transfer" and value > 0:
            attached = value
        if op == "excess" and value > 0 and (not dest or addresses_equal(dest, user_wallet)):
            refund += value
    net = max(0, attached - refund - max(0, offer_gram_nano))
    latency = max(0, max(utimes) - min(utimes)) if utimes else 0
    return {
        "attached_gram_nano": attached,
        "refund_gram_nano": refund,
        "net_cost_gram_nano": net,
        "latency_s": latency,
        "hops": hops,
    }


class GasMeasurer:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._owns = client is None
        self.client = client or httpx.Client(
            timeout=settings.http_timeout_s,
            headers={"Accept": "application/json", "User-Agent": "ton-gram-paper/1.0"},
        )

    def close(self) -> None:
        if self._owns:
            self.client.close()

    def fetch_events(self, limit: int = 80) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        before_lt: int | None = None
        pages = 0
        while len(collected) < limit and pages < 12:
            params: dict[str, Any] = {"limit": 25}
            if before_lt:
                params["before_lt"] = before_lt
            url = f"{self.settings.tonapi_base_url}/v2/accounts/{PINNED_POOL_ADDRESS}/events"
            response = self.client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("events") or []
            pages += 1
            if not batch:
                break
            collected.extend(batch)
            before_lt = payload.get("next_from")
            if not before_lt:
                break
        return collected

    def fetch_trace(self, event_id: str) -> dict[str, Any]:
        url = f"{self.settings.tonapi_base_url}/v2/traces/{event_id}"
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                response = self.client.get(url)
                if response.status_code == 429:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.3 * (attempt + 1))
        raise last_exc or RuntimeError(f"trace {event_id} failed")

    def collect(self, target: int = 60) -> list[SwapSample]:
        events = self.fetch_events(limit=max(target * 3, 120))
        candidates: list[tuple[dict[str, Any], str, int, str]] = []
        for event in events:
            if event.get("in_progress"):
                continue
            for action in event.get("actions") or []:
                classified = classify_swap(action)
                if classified is None:
                    continue
                direction, offer = classified
                user = str(((action.get("JettonSwap") or {}).get("user_wallet") or {}).get("address") or "")
                candidates.append((event, direction, offer, user))
                break
        candidates = candidates[: max(target * 2, 80)]
        samples: list[SwapSample] = []

        def _one(item: tuple[dict[str, Any], str, int, str]) -> SwapSample | None:
            event, direction, offer, user = item
            try:
                trace = self.fetch_trace(event["event_id"])
            except Exception as exc:  # noqa: BLE001
                log.warning("trace %s failed: %s", event.get("event_id"), exc)
                return None
            costs = extract_costs(trace, user, offer)
            return SwapSample(
                event_id=str(event["event_id"]),
                direction=direction,
                timestamp_utc=_utc_from_unix(int(event.get("timestamp") or 0)),
                user_wallet=user,
                attached_gram_nano=costs["attached_gram_nano"],
                offer_gram_nano=offer,
                refund_gram_nano=costs["refund_gram_nano"],
                net_cost_gram_nano=costs["net_cost_gram_nano"],
                latency_s=costs["latency_s"],
                hops=costs["hops"],
                success=True,
            )

        workers = min(4, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, item) for item in candidates]
            for future in as_completed(futures):
                sample = future.result()
                if (
                    sample is not None
                    and sample.attached_gram_nano > 0
                    and sample.net_cost_gram_nano <= 250_000_000
                ):
                    samples.append(sample)
        samples.sort(key=lambda s: s.timestamp_utc, reverse=True)
        return samples


def build_profile(samples: list[SwapSample], settings: Settings) -> dict[str, Any]:
    nets = [s.net_cost_gram_nano for s in samples]
    lats = [float(s.latency_s) for s in samples]
    attached = [s.attached_gram_nano for s in samples]
    refunds = [s.refund_gram_nano for s in samples]
    net_stats = summarize_ints(nets)
    lat_stats = summarize_floats(lats)
    p90_lat = lat_stats["p90"]
    applied_delay = int(max(1, round(p90_lat)) + settings.latency_stress_margin_s)
    applied_cost = net_stats["p90"] + (net_stats["p90"] * settings.network_cost_stress_margin_bps) // 10_000
    by_dir: dict[str, Any] = {}
    for sample in samples:
        bucket = by_dir.setdefault(sample.direction, {"n": 0, "nets": [], "lats": []})
        bucket["n"] += 1
        bucket["nets"].append(sample.net_cost_gram_nano)
        bucket["lats"].append(float(sample.latency_s))
    direction_stats = {
        name: {
            "n": bucket["n"],
            "net_cost_gram_nano": summarize_ints(bucket["nets"]),
            "latency_s": summarize_floats(bucket["lats"]),
        }
        for name, bucket in by_dir.items()
    }
    return {
        "as_of_utc": format_utc(datetime.now(timezone.utc)),
        "source": "measured",
        "pool": PINNED_POOL_ADDRESS,
        "router": PINNED_ROUTER_ADDRESS,
        "sample_n": len(samples),
        "directions": {name: bucket["n"] for name, bucket in direction_stats.items()},
        "by_direction": direction_stats,
        "attached_gram_nano": summarize_ints(attached),
        "refund_gram_nano": summarize_ints(refunds),
        "net_cost_gram_nano": net_stats,
        "latency_s": lat_stats,
        "applied": {
            "delay_stress_s": applied_delay,
            "delay_adverse_bps": 2,
            "measured_net_cost_p90_nano": net_stats["p90"],
            "network_cost_gram_nano": applied_cost,
            "network_cost_stress_margin_bps": settings.network_cost_stress_margin_bps,
            "latency_stress_margin_s": settings.latency_stress_margin_s,
        },
        "notes": [
            "Attached GRAM is the first outbound from the user wallet (pton_ton_transfer or jetton_transfer).",
            "Refund is the sum of excess messages back to the user wallet.",
            "Net cost = attached - refund - native offer (GRAM->USDT only). Combined p90 is dominated by USDT->GRAM gas attach.",
            "Latency is first-trace utime to last-trace utime (TonAPI whole seconds; most swaps finish in the same second).",
        ],
    }


def apply_profile(settings: Settings, profile: dict[str, Any]) -> Settings:
    applied = profile.get("applied") or {}
    if applied.get("delay_stress_s"):
        settings.delay_stress_s = int(applied["delay_stress_s"])
    if "delay_adverse_bps" in applied:
        settings.delay_adverse_bps = int(applied["delay_adverse_bps"])
    if applied.get("measured_net_cost_p90_nano"):
        settings.measured_net_cost_p90_nano = int(applied["measured_net_cost_p90_nano"])
    settings.cost_model_source = "measured"
    return settings


def write_csv(samples: list[SwapSample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(samples[0]).keys()) if samples else [
        "event_id",
        "direction",
        "timestamp_utc",
        "user_wallet",
        "attached_gram_nano",
        "offer_gram_nano",
        "refund_gram_nano",
        "net_cost_gram_nano",
        "latency_s",
        "hops",
        "success",
        "source",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow(asdict(sample))


def write_profile(profile: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n")


def load_profile(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def default_profile_path(settings: Settings) -> Path:
    return settings.data_dir / "gas_profile.json"


def default_csv_path(settings: Settings) -> Path:
    return settings.data_dir / "gas_refunds.csv"
