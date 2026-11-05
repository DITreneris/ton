"""Integer-safe percentile helpers for measurement reports."""

from __future__ import annotations


def percentile(values: list[int] | list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low]) + (float(ordered[high]) - float(ordered[low])) * frac


def percentile_int(values: list[int], p: float) -> int:
    return int(round(percentile(values, p)))


def summarize_ints(values: list[int]) -> dict[str, int]:
    if not values:
        return {"n": 0, "median": 0, "p90": 0, "max": 0}
    return {
        "n": len(values),
        "median": percentile_int(values, 50),
        "p90": percentile_int(values, 90),
        "max": max(values),
    }


def summarize_floats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "median": 0.0, "p90": 0.0, "max": 0.0}
    return {
        "n": float(len(values)),
        "median": round(percentile(values, 50), 3),
        "p90": round(percentile(values, 90), 3),
        "max": round(max(values), 3),
    }
