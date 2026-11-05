from ton_gram.config import Settings
from ton_gram.scope import verify_scope
from tests.conftest import (
    lookalike_usdt_address,
    native_gram_asset,
    official_pool,
    official_router,
    official_usdt_asset,
)


def _quotes():
    router = official_router()
    return [
        {
            "router_address": router["address"],
            "pool_address": official_pool()["address"],
            "router": router,
        }
    ]


def test_official_scope_passes(settings: Settings):
    scope = verify_scope(
        settings,
        observed_network="mainnet",
        usdt_asset=official_usdt_asset(),
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=_quotes(),
    )
    assert scope.passed


def test_ticker_lookalike_fails_closed(settings: Settings):
    fake = official_usdt_asset()
    fake["contract_address"] = lookalike_usdt_address()
    fake["symbol"] = "USD₮"
    scope = verify_scope(
        settings,
        observed_network="mainnet",
        usdt_asset=fake,
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=_quotes(),
    )
    assert not scope.passed
    assert any(c.name == "usdt_ticker_lookalike" and not c.passed for c in scope.checks)


def test_wrong_network_fails(settings: Settings):
    scope = verify_scope(
        settings,
        observed_network="testnet",
        usdt_asset=official_usdt_asset(),
        native_asset=native_gram_asset(),
        pool=official_pool(),
        router=official_router(),
        quotes=_quotes(),
    )
    assert not scope.passed
    assert any(c.name == "network" and not c.passed for c in scope.checks)


def test_wrong_pool_fails(settings: Settings):
    pool = official_pool()
    pool["address"] = lookalike_usdt_address()
    scope = verify_scope(
        settings,
        observed_network="mainnet",
        usdt_asset=official_usdt_asset(),
        native_asset=native_gram_asset(),
        pool=pool,
        router=official_router(),
        quotes=_quotes(),
    )
    assert not scope.passed
