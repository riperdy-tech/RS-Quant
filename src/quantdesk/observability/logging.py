from __future__ import annotations

import logging
import re
from typing import Any

SENSITIVE_KEYS = {
    "api_key",
    "secret",
    "secret_key",
    "passphrase",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "auth",
    "credentials",
    "private_key",
    "key",
}

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|passphrase|token|password)=([^&\s]+)"),
    re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9_\-\.=]+)"),
]


def redact_sensitive_data(obj: Any) -> Any:
    """Recursively redacts secrets, credentials, and API keys from logging payloads."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS:
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_sensitive_data(v)
        return cleaned
    elif isinstance(obj, (list, tuple)):
        return [redact_sensitive_data(item) for item in obj]
    elif isinstance(obj, str):
        result = obj
        for pattern in SENSITIVE_PATTERNS:
            result = pattern.sub(r"\1=[REDACTED]", result)
        return result
    return obj


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive fields from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_data(record.msg)
        if isinstance(record.args, dict):
            record.args = redact_sensitive_data(record.args)
        return True


SecretRedactionFilter = RedactingFilter


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Sets up root structured logging with secret redaction."""
    logger = logging.getLogger("quantdesk")
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

    return logger


logger = setup_logging()
