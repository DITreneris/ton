"""Process logging. Keep httpx off the cycle line."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_NOISY = ("httpx", "httpcore", "hpack", "httpcore.connection", "httpcore.http11")


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
