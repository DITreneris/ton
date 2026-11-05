from ton_gram.alerts import TelegramAlerter


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, url, json):
        self.calls += 1

        class Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "message_id": 1}

        return Resp()


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
