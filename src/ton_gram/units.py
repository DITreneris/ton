"""Integer base units. GRAM uses 9 decimals (nano). USD₮ uses 6 decimals (micro)."""

from __future__ import annotations

GRAM_DECIMALS = 9
USDT_DECIMALS = 6
GRAM_SCALE = 10**GRAM_DECIMALS
USDT_SCALE = 10**USDT_DECIMALS
BPS_SCALE = 10_000


def require_int(name: str, value: int | str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer base-unit amount")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    raise ValueError(f"{name} is not an integer base-unit amount: {value!r}")


def non_negative_int(name: str, value: int | str) -> int:
    parsed = require_int(name, value)
    if parsed < 0:
        raise ValueError(f"{name} must be >= 0")
    return parsed


def apply_bps(amount: int, bps: int) -> int:
    """Return amount * bps / 10_000 using integer truncation toward zero."""
    return (int(amount) * int(bps)) // BPS_SCALE


def reduce_by_bps(amount: int, bps: int) -> int:
    if bps < 0:
        raise ValueError("bps must be >= 0")
    reduced = amount - apply_bps(amount, bps)
    return max(0, reduced)


def bps_delta(current: int, reference: int) -> int:
    """Signed basis-point change of current vs reference. 0 if reference is 0."""
    if reference == 0:
        return 0
    return ((current - reference) * BPS_SCALE) // reference


def usdt_per_gram_micro(gram_nano: int, usdt_micro: int) -> int:
    """USDT microunits received per 1 GRAM (1e9 nano), integer."""
    if gram_nano <= 0:
        return 0
    return (usdt_micro * GRAM_SCALE) // gram_nano


def gram_per_usdt_nano(usdt_micro: int, gram_nano: int) -> int:
    """GRAM nano received per 1 USDT (1e6 micro), integer."""
    if usdt_micro <= 0:
        return 0
    return (gram_nano * USDT_SCALE) // usdt_micro


def usdt_value_micro(gram_nano: int, usdt_per_gram_micro: int) -> int:
    """Convert a GRAM nano amount to USD₮ micro at a micro-per-GRAM mid.

    Do not use usdt_per_gram_micro() for this: that helper computes a *price*
    from a swap pair, and inverting (gas_nano, mid) books ~GRAM_SCALE/gas too much.
    """
    if gram_nano <= 0 or usdt_per_gram_micro <= 0:
        return 0
    return (int(gram_nano) * int(usdt_per_gram_micro)) // GRAM_SCALE


def mark_usdt_micro(gram_nano: int, usdt_micro: int, usdt_per_gram_micro: int) -> int:
    return usdt_micro + usdt_value_micro(gram_nano, usdt_per_gram_micro)


def gram_from_usdt(usdt_micro: int, usdt_per_gram_micro: int) -> int:
    if usdt_per_gram_micro <= 0:
        return 0
    return (usdt_micro * GRAM_SCALE) // usdt_per_gram_micro
