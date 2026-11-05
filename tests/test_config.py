import os

from ton_gram.config import Settings, resolve_listen_port


def test_resolve_listen_port_prefers_ton_gram_port(monkeypatch):
    monkeypatch.setenv("TON_GRAM_PORT", "43177")
    monkeypatch.setenv("PORT", "8080")
    assert resolve_listen_port() == 43177


def test_resolve_listen_port_falls_back_to_railway_port(monkeypatch):
    monkeypatch.delenv("TON_GRAM_PORT", raising=False)
    monkeypatch.setenv("PORT", "8080")
    assert resolve_listen_port() == 8080


def test_resolve_listen_port_default(monkeypatch):
    monkeypatch.delenv("TON_GRAM_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert resolve_listen_port() == 43177


def test_settings_uses_railway_port_when_ton_gram_port_unset(monkeypatch):
    monkeypatch.delenv("TON_GRAM_PORT", raising=False)
    monkeypatch.setenv("PORT", "9090")
    assert Settings().port == 9090


def test_settings_ton_gram_port_wins(monkeypatch):
    monkeypatch.setenv("TON_GRAM_PORT", "43177")
    monkeypatch.setenv("PORT", "9090")
    assert Settings().port == 43177


def test_settings_explicit_port_kwarg_wins(monkeypatch):
    monkeypatch.delenv("TON_GRAM_PORT", raising=False)
    monkeypatch.setenv("PORT", "9090")
    assert Settings(port=1234).port == 1234
    assert os.environ.get("PORT") == "9090"
