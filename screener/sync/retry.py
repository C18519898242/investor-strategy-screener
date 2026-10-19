import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

RETRYABLE_MARKERS: tuple[str, ...] = (
    "rate_limited",
    "upstream_pool_exhausted",
    "timed out",
    "connection",
)
RATE_LIMITED_WAIT = 30
RETRY_WAIT = 5


def call_with_retry[T](
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """认得出的临时故障一直重试下去，认不出的异常原样抛出。

    不设次数上限：一次上游抖动不该让十几个小时的回填整轮作废。间隔固定，
    限频单独等久一点（上游自己回的就是「建议等待 30 秒后重试」）。等待期间
    `KeyboardInterrupt` 照旧能打断——`except Exception` 不捕它。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            result = operation()
        except Exception as error:
            message = str(error).lower()
            marker = next((name for name in RETRYABLE_MARKERS if name in message), None)
            if marker is None:
                raise
            wait = RATE_LIMITED_WAIT if marker == "rate_limited" else RETRY_WAIT
            logger.warning("第 %d 次调用失败（%s），%d 秒后重试", attempt, marker, wait)
            sleep(wait)
        else:
            if attempt > 1:
                logger.debug("第 %d 次尝试成功", attempt)
            return result
