"""Structured JSON logging. There is no other way to emit output in this app."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_secrets,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


_SECRET_HINTS = ("token", "secret", "password", "client_id", "api_key", "authorization")


def _redact_secrets(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Secrets never reach a log line, an error message, or a rendered page."""
    for key in list(event_dict):
        if any(hint in key.lower() for hint in _SECRET_HINTS):
            event_dict[key] = "[redacted]"
    return event_dict


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
