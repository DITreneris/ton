"""Optional outbound Telegram alerts. Off by default. Deduped. Never inbound commands."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ton_gram.config import Settings
from ton_gram.ledger import Ledger

log = logging.getLogger("ton_gram.alerts")

# Telegram answers 404 for an unknown bot token (not a missing chat).
_REJECT_STATUSES = {401, 403, 404}


class TelegramAlerter:
    def __init__(self, settings: Settings, ledger: Ledger, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.ledger = ledger
        self.client = client or httpx.Client(timeout=settings.http_timeout_s)
        self._rejected = False

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
        """Never raise: a bad Telegram token must not abort a paper cycle."""
        if not (self.enabled or (force and self.configured)):
            return {"skipped": True, "reason": "disabled"}
        if self._rejected and not force:
            return {"skipped": True, "reason": "rejected"}
        if not force and self.ledger.recent_alert(alert_key, self.settings.telegram_dedupe_s):
            return {"skipped": True, "reason": "deduped"}
        token = (self.settings.telegram_bot_token or "").strip().strip('"').strip("'")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            response = self.client.post(
                url,
                json={
                    "chat_id": str(self.settings.telegram_chat_id).strip(),
                    "text": text[:3500],
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("telegram send failed key=%s error=%s", alert_key, exc)
            self.ledger.record_alert(alert_key)
            return {"skipped": True, "reason": "http_error", "error": str(exc)}
        if response.status_code in _REJECT_STATUSES:
            self._rejected = True
            log.error(
                "telegram rejected outbound status=%s key=%s; disable TON_GRAM_TELEGRAM_ENABLED "
                "or fix TON_GRAM_TELEGRAM_BOT_TOKEN (404 usually means the bot token is invalid)",
                response.status_code,
                alert_key,
            )
            self.ledger.record_alert(alert_key)
            return {"skipped": True, "reason": "rejected", "status": response.status_code}
        if response.status_code >= 400:
            log.warning(
                "telegram send status=%s key=%s body=%s",
                response.status_code,
                alert_key,
                (response.text or "")[:200],
            )
            self.ledger.record_alert(alert_key)
            return {"skipped": True, "reason": "http_error", "status": response.status_code}
        self.ledger.record_alert(alert_key)
        try:
            payload = response.json()
        except ValueError:
            payload = {"ok": True}
        return {"skipped": False, "telegram": payload}
