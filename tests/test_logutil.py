import logging

from ton_gram.logutil import RedactSecrets, configure_logging


def test_redact_secrets_strips_telegram_bot_token():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="HTTP Request: POST https://api.telegram.org/bot111:AASECRET/sendMessage",
        args=(),
        exc_info=None,
    )
    assert RedactSecrets().filter(record) is True
    assert "AASECRET" not in record.getMessage()
    assert "api.telegram.org/bot***/sendMessage" in record.getMessage()


def test_configure_logging_silences_httpx():
    configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert any(isinstance(f, RedactSecrets) for f in logging.getLogger().filters)
