"""
retry.py — Universal retry logic for all external API calls.

Retry schedule: immediate → 10s → 20s → give up.
Use as a decorator or call retry_call() directly.
"""

import time
import functools

RETRY_DELAYS = [0, 10, 20]  # seconds to wait before each attempt


def retry_call(fn, *args, is_error=None, label="api", **kwargs):
    """
    Call fn(*args, **kwargs) with retries on failure.

    is_error: optional callable(result) -> bool that checks whether the
              return value indicates a soft failure (e.g. {"error": ...}).
              If None, only exceptions trigger retries.
    label:    short name for log output (e.g. "kraken", "llm", "x_search").
    """
    last_exc = None
    for attempt, delay in enumerate(RETRY_DELAYS, 1):
        if delay > 0:
            print(f"  [retry] {label}: attempt {attempt}/{len(RETRY_DELAYS)} in {delay}s...")
            time.sleep(delay)
        try:
            result = fn(*args, **kwargs)
            if is_error and is_error(result):
                last_exc = result
                errmsg = result.get("error", "unknown") if isinstance(result, dict) else str(result)
                print(f"  [retry] {label}: soft error on attempt {attempt} — {str(errmsg)[:120]}")
                continue
            return result
        except Exception as e:
            last_exc = e
            print(f"  [retry] {label}: exception on attempt {attempt} — {type(e).__name__}: {str(e)[:120]}")
    # All attempts exhausted
    if isinstance(last_exc, Exception):
        raise last_exc
    return last_exc  # return last soft-error result


def with_retries(label="api", is_error=None):
    """
    Decorator version. Wraps a function so every call gets automatic retries.

    @with_retries(label="kraken", is_error=lambda r: isinstance(r, dict) and "error" in r)
    def run_kraken(args): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return retry_call(fn, *args, is_error=is_error, label=label, **kwargs)
        return wrapper
    return decorator
