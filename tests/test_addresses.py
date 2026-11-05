from ton_gram.addresses import addresses_equal, is_native_ton, parse_address
from ton_gram.config import NATIVE_GRAM_ADDRESS, OFFICIAL_USDT_JETTON_MASTER


def test_official_usdt_roundtrip():
    parsed = parse_address(OFFICIAL_USDT_JETTON_MASTER)
    assert parsed.workchain == 0
    assert addresses_equal(parsed.to_friendly(True), OFFICIAL_USDT_JETTON_MASTER)
    assert addresses_equal(parsed.to_friendly(False), OFFICIAL_USDT_JETTON_MASTER)
    assert addresses_equal(parsed.to_raw(), OFFICIAL_USDT_JETTON_MASTER)


def test_native_gram_is_zero_address():
    assert is_native_ton(NATIVE_GRAM_ADDRESS)
    assert not is_native_ton(OFFICIAL_USDT_JETTON_MASTER)
