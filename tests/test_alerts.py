from ton_gram.alerts import TelegramAlerter


class FakeResp:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True, "message_id": 1}
        self.text = text or str(status_code)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, status_code: int = 200) -> None:
        self.calls = 0
        self.status_code = status_code

    def post(self, url, json):
        self.calls += 1
        return FakeResp(self.status_code)


def test_telegram_dedupes(settings, ledger):
    settings.telegram_enabled = True
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    settings.telegram_dedupe_s = 3600
    client = FakeClient()
    alerter = TelegramAlerter(settings, ledger, client=client)
    first = alerter.send("halt", "halt one")
    second = alerter.send("halt", "halt two")
    assert first["skipped"] is False
    assert second["skipped"] is True
    assert second["reason"] == "deduped"
    assert client.calls == 1


def test_telegram_test_force_bypasses_dedupe(settings, ledger):
    settings.telegram_enabled = False
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    client = FakeClient()
    alerter = TelegramAlerter(settings, ledger, client=client)
    first = alerter.send("telegram_test", "hi", force=True)
    second = alerter.send("telegram_test", "hi again", force=True)
    assert first["skipped"] is False
    assert second["skipped"] is False
    assert client.calls == 2


def test_telegram_404_does_not_raise_and_stops_retrying(settings, ledger):
    settings.telegram_enabled = True
    settings.telegram_bot_token = "bad-token"
    settings.telegram_chat_id = "1"
    client = FakeClient(status_code=404)
    alerter = TelegramAlerter(settings, ledger, client=client)
    first = alerter.send("daily_summary_2026-11-05", "summary")
    second = alerter.send("halt", "halt")
    assert first["skipped"] is True
    assert first["reason"] == "rejected"
    assert first["status"] == 404
    assert second["skipped"] is True
    assert second["reason"] == "rejected"
    assert client.calls == 1
    assert ledger.recent_alert("daily_summary_2026-11-05", 3600) is False


def test_worker_cycle_survives_telegram_404(settings, clock, ledger):
    from unittest.mock import patch

    from ton_gram.poller import ProviderError
    from ton_gram.worker import PaperWorker

    settings.telegram_enabled = True
    settings.telegram_bot_token = "bad-token"
    settings.telegram_chat_id = "1"
    client = FakeClient(status_code=404)
    worker = PaperWorker(settings, clock, ledger, alerter=TelegramAlerter(settings, ledger, client=client))
    with patch.object(worker.poller, "fetch_scope_inputs", side_effect=ProviderError("offline")):
        summary = worker.cycle()
    assert summary["snapshot_id"]
    assert "skip" in summary
    assert client.calls == 1
    worker.close()


def test_failed_telegram_does_not_consume_daily_summary(settings, clock, ledger):
    from ton_gram.clock import format_utc
    from ton_gram.worker import PaperWorker

    settings.telegram_enabled = True
    settings.telegram_bot_token = "tok"
    settings.telegram_chat_id = "1"
    day = format_utc(clock.now())[:10]
    key = f"daily_summary_sent_{day}"
    bad = FakeClient(status_code=404)
    worker = PaperWorker(settings, clock, ledger, alerter=TelegramAlerter(settings, ledger, client=bad))
    worker._maybe_daily_summary()
    assert bad.calls == 1
    assert ledger.recent_alert(key, 36 * 3600) is False
    good = FakeClient(status_code=200)
    worker.alerter = TelegramAlerter(settings, ledger, client=good)
    worker._maybe_daily_summary()
    assert good.calls == 1
    assert ledger.recent_alert(key, 36 * 3600) is True
    worker.close()


def test_configure_logging_silences_httpx():
    import logging

    from ton_gram.logutil import configure_logging

    configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("ton_gram.worker").getEffectiveLevel() == logging.INFO
