"""Process logging. Keep httpx off the cycle line. Never log Telegram bot tokens.

Railway tags stderr as severity=error. INFO/WARNING go to stdout so a healthy
cycle is not a red card. ERROR and above stay on stderr.
"""

from __future__ import annotations

import logging
import re
import sys

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


class _MaxLevelFilter(logging.Filter):
    """Keep records strictly below a level (stdout path)."""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < self.max_level


def configure_logging(level: int = logging.INFO) -> None:
    redact = RedactSecrets()
    formatter = logging.Formatter(LOG_FORMAT)

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setLevel(level)
    stdout.addFilter(_MaxLevelFilter(logging.ERROR))
    stdout.addFilter(redact)
    stdout.setFormatter(formatter)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.ERROR)
    stderr.addFilter(redact)
    stderr.setFormatter(formatter)

    logging.basicConfig(level=level, format=LOG_FORMAT, force=True, handlers=[stdout, stderr])
    root = logging.getLogger()
    if not any(isinstance(f, RedactSecrets) for f in root.filters):
        root.addFilter(redact)
    for name in _NOISY:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        if not any(isinstance(f, RedactSecrets) for f in logger.filters):
            logger.addFilter(redact)
