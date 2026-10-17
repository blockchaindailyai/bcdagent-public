from __future__ import annotations

import time

MIN_PRICE_LOOKUP_BACKOFF_SECONDS = 180.0

_GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC = 0.0
_CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES = 0
_HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE = False


def is_price_lookup_backoff_active(now_monotonic: float | None = None) -> bool:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    return now < _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC


def get_price_lookup_backoff_remaining_seconds(now_monotonic: float | None = None) -> float:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    return max(0.0, _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC - now)


def activate_price_lookup_backoff(
    *,
    retry_after_seconds: float | None = None,
    minimum_backoff_seconds: float = MIN_PRICE_LOOKUP_BACKOFF_SECONDS,
    now_monotonic: float | None = None,
) -> float:
    now = now_monotonic if now_monotonic is not None else time.monotonic()
    safe_retry_after = max(0.0, float(retry_after_seconds or 0.0))
    backoff_seconds = max(float(minimum_backoff_seconds), safe_retry_after)

    global _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC
    _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC = max(
        _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC,
        now + backoff_seconds,
    )
    return backoff_seconds


def register_price_lookup_backoff_failure(log_after_attempts: int = 3) -> bool:
    """Track a consecutive rate-limit failure and indicate if it should be logged."""
    global _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES
    global _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE

    _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES += 1
    threshold = max(1, int(log_after_attempts))
    should_log = (
        _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES >= threshold
        and not _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE
    )
    if should_log:
        _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE = True
    return should_log


def register_price_lookup_backoff_success() -> None:
    global _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES
    global _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE
    _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES = 0
    _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE = False


def reset_price_lookup_backoff_for_tests() -> None:
    global _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC
    global _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES
    global _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE
    _GLOBAL_PRICE_LOOKUP_BACKOFF_UNTIL_MONOTONIC = 0.0
    _CONSECUTIVE_PRICE_LOOKUP_BACKOFF_FAILURES = 0
    _HAS_LOGGED_PRICE_LOOKUP_BACKOFF_FAILURE = False
