"""Structured logging with structlog. Secrets are redacted before anything is rendered."""

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "credentials",
        "database_url",
        "dsn",
        "espn_s2",
        "password",
        "secret",
        "swid",
        "token",
    }
)
_SECRET_SUFFIXES = ("_password", "_secret", "_token", "api_key", "encryption_key", "_cookie")

REDACTED = "[redacted]"


def redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: blank out values whose key names a secret."""
    for key in event_dict:
        lowered = key.lower()
        if lowered in _SECRET_KEYS or lowered.endswith(_SECRET_SUFFIXES):
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = False) -> None:
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
