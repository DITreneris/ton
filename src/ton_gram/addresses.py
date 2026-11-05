"""TON address identity. Tickers and display names are never used for matching."""

from __future__ import annotations

import base64
from dataclasses import dataclass

CRC_POLY = 0x1021


def crc16_ccitt(data: bytes) -> bytes:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc.to_bytes(2, "big")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid base64 address: {text}") from exc
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid address encoding: {text}") from exc


@dataclass(frozen=True, slots=True)
class TonAddress:
    workchain: int
    account_id: bytes

    def __post_init__(self) -> None:
        if len(self.account_id) != 32:
            raise ValueError("account id must be 32 bytes")
        if not -128 <= self.workchain <= 127:
            raise ValueError("workchain out of range")

    @property
    def key(self) -> str:
        return f"{self.workchain}:{self.account_id.hex()}"

    def to_friendly(self, bounceable: bool = True) -> str:
        tag = 0x11 if bounceable else 0x51
        wc = self.workchain & 0xFF
        payload = bytes([tag, wc]) + self.account_id
        raw = payload + crc16_ccitt(payload)
        return base64.b64encode(raw).decode("ascii")

    def to_raw(self) -> str:
        return f"{self.workchain}:{self.account_id.hex()}"


def parse_address(value: str) -> TonAddress:
    text = (value or "").strip()
    if not text:
        raise ValueError("empty address")
    if text.startswith(("0:", "-1:")) or (":" in text and not text.startswith(("EQ", "UQ", "kQ", "0Q"))):
        wc_s, hex_part = text.split(":", 1)
        account = bytes.fromhex(hex_part)
        if len(account) != 32:
            raise ValueError(f"raw address hash must be 32 bytes: {text}")
        return TonAddress(int(wc_s), account)

    raw = None
    padded = text + "=" * (-len(text) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(padded)
            break
        except Exception:
            continue
    if raw is None or len(raw) != 36:
        raise ValueError(f"unsupported TON address: {text}")
    payload, crc = raw[:34], raw[34:]
    if crc16_ccitt(payload) != crc:
        raise ValueError(f"address checksum mismatch: {text}")
    workchain = payload[1] if payload[1] < 128 else payload[1] - 256
    return TonAddress(workchain, payload[2:34])


def address_key(value: str) -> str:
    return parse_address(value).key


def addresses_equal(left: str, right: str) -> bool:
    try:
        return address_key(left) == address_key(right)
    except ValueError:
        return False


def encode_account(workchain: int, account_hex: str, bounceable: bool = True) -> str:
    return TonAddress(workchain, bytes.fromhex(account_hex)).to_friendly(bounceable)


def is_native_ton(value: str) -> bool:
    try:
        parsed = parse_address(value)
    except ValueError:
        return False
    return parsed.workchain == 0 and parsed.account_id == bytes(32)
