import time
from collections.abc import Callable
from typing import TypeVar

from screener.sync.pipeline import SyncError

T = TypeVar("T")

RETRYABLE_MARKERS: tuple[str, ...] = (
    "rate_limited",
    "upstream_pool_exhausted",
    "timed out",
    "connection",
)
RATE_LIMITED_WAIT = 30
BACKOFF_WAITS = (5, 15)


def call_with_retry(
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 3,
) -> T:
    """代理错误按固定退避重试；认不出的异常原样抛出，不重试。"""
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            message = str(error).lower()
            if not any(marker in message for marker in RETRYABLE_MARKERS):
                raise
            if attempt == attempts - 1:
                raise SyncError(f"重试 {attempts} 次后仍失败：{error}") from error
            if "rate_limited" in message:
                sleep(RATE_LIMITED_WAIT)
            else:
                sleep(BACKOFF_WAITS[min(attempt, len(BACKOFF_WAITS) - 1)])
    raise AssertionError("attempts 必须大于 0")
