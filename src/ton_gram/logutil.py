"""Process logging. Keep httpx off the cycle line. Never log Telegram bot tokens."""

from __future__ import annotations

import logging
import re

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_NOISY = ("httpx", "httpcore", "hpack", "httpcore.connection", "httpcore.http11")
_BOT_URL = re.compile(r"(https://api\.telegram\.org/bot)[^/\s]+")


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if "api.telegram.org/bot" not in text:
            return True
        record.msg = _BOT_URL.sub(r"\1***", text)
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    redact = RedactSecrets()
    root = logging.getLogger()
    if not any(isinstance(f, RedactSecrets) for f in root.filters):
        root.addFilter(redact)
    for name in _NOISY:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        if not any(isinstance(f, RedactSecrets) for f in logger.filters):
            logger.addFilter(redact)
