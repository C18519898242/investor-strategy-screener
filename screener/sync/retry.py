import logging
import time
from collections.abc import Callable

from screener.sync.errors import SyncError

logger = logging.getLogger(__name__)

RETRYABLE_MARKERS: tuple[str, ...] = (
    "rate_limited",
    "upstream_pool_exhausted",
    "timed out",
    "connection",
)
RATE_LIMITED_WAIT = 30
BACKOFF_WAITS = (5, 15)


def call_with_retry[T](
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 3,
) -> T:
    """代理错误按固定退避重试；认不出的异常原样抛出，不重试。"""
    for attempt in range(attempts):
        try:
            result = operation()
        except Exception as error:
            message = str(error).lower()
            marker = next((name for name in RETRYABLE_MARKERS if name in message), None)
            if marker is None:
                raise
            if attempt == attempts - 1:
                raise SyncError(f"重试 {attempts} 次后仍失败：{error}") from error
            if marker == "rate_limited":
                wait = RATE_LIMITED_WAIT
            else:
                wait = BACKOFF_WAITS[min(attempt, len(BACKOFF_WAITS) - 1)]
            logger.warning("第 %d 次调用失败（%s），%d 秒后重试", attempt + 1, marker, wait)
            sleep(wait)
        else:
            if attempt > 0:
                logger.debug("第 %d 次尝试成功", attempt + 1)
            return result
    raise AssertionError("attempts 必须大于 0")
