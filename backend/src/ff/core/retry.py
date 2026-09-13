"""Retry policy for sources that throttle without telling you when to come back."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from ff.core.errors import SourceUnavailable, Throttled
from ff.core.logging import get_logger

T = TypeVar("T")
log = get_logger(__name__)

#: Yahoo's throttle signal. Non-standard, and the body is HTML rather than JSON -- which is
#: why every call must check status_code before parsing.
YAHOO_THROTTLE_STATUS = 999

RETRYABLE_EXCEPTIONS = (Throttled, ConnectionError, TimeoutError)


def with_backoff(
    fn: Callable[[], T],
    *,
    source: str,
    attempts: int = 4,
    base_delay_s: float = 3.0,
    max_delay_s: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Exponential backoff with jitter, in seconds not milliseconds.

    Sub-second retries against a throttle just deepen it. There is no Retry-After header to
    read, and throttling is keyed to the app id rather than the user token, so a tight loop
    burns the whole integration rather than one request.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except RETRYABLE_EXCEPTIONS as exc:
            last = exc
            if attempt == attempts - 1:
                break
            delay = min(base_delay_s * (2**attempt), max_delay_s)
            delay *= 0.5 + random.random()
            log.warning("retrying", source=source, attempt=attempt + 1, delay_s=round(delay, 2))
            sleep(delay)
    raise SourceUnavailable(source, f"failed after {attempts} attempts: {last}", required=True)
