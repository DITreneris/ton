"""Optional outbound Telegram alerts. Off by default. Deduped. Never inbound commands."""

from __future__ import annotations

from typing import Any

import httpx

from ton_gram.config import Settings
from ton_gram.ledger import Ledger


class TelegramAlerter:
    def __init__(self, settings: Settings, ledger: Ledger, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.ledger = ledger
        self.client = client or httpx.Client(timeout=settings.http_timeout_s)

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.telegram_enabled
            and self.settings.telegram_bot_token
            and self.settings.telegram_chat_id
        )

    @property
    def configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def send(self, alert_key: str, text: str, *, force: bool = False) -> dict[str, Any]:
        if not (self.enabled or (force and self.configured)):
            return {"skipped": True, "reason": "disabled"}
        if not force and self.ledger.recent_alert(alert_key, self.settings.telegram_dedupe_s):
            return {"skipped": True, "reason": "deduped"}
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        response = self.client.post(
            url,
            json={
                "chat_id": self.settings.telegram_chat_id,
                "text": text[:3500],
                "disable_web_page_preview": True,
            },
        )
        response.raise_for_status()
        self.ledger.record_alert(alert_key)
        return {"skipped": False, "telegram": response.json()}
