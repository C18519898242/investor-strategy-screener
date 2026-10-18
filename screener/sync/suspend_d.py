import logging
import sqlite3
import time
from datetime import date, datetime, timedelta

from screener.store import delete_rows, insert_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "停复牌"
COLUMNS: tuple[str, ...] = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
CHUNK_DAYS = 27
BACK_YEARS = 11


def fetch_suspend_d(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """从水位那天起按 27 天分段补到今天：水位那天的旧行先删掉再重写。

    水位晚于今天时什么都不做——那种水位只可能来自上游返回的未来日期，
    删掉它又不重取，等于白丢一天的数据。
    """
    end = today or date.today()
    water = connection.execute("SELECT MAX(trade_date) FROM suspend_d").fetchone()[0]
    if water is None:
        start = end.replace(year=end.year - BACK_YEARS)
    else:
        start = datetime.strptime(water, "%Y%m%d").date()
        if start > end:
            logger.debug("suspend_d 水位 %s 晚于今天 %s，不删也不补", water, end.strftime("%Y%m%d"))
            return 0
        logger.debug("suspend_d 水位 %s，先删掉那天的旧行", water)
        delete_rows(connection, table="suspend_d", column="trade_date", value=water)

    written = 0
    total = (end - start).days // CHUNK_DAYS + 1
    index = 0
    chunk_start = start
    while chunk_start <= end:
        index += 1
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        start_text = chunk_start.strftime("%Y%m%d")
        end_text = chunk_end.strftime("%Y%m%d")
        logger.info("停复牌 第 %d/%d 段：%s ~ %s", index, total, start_text, end_text)
        logger.debug("suspend_d 请求：%s ~ %s", start_text, end_text)
        started = time.perf_counter()
        frame = call_with_retry(
            lambda start_date=start_text, end_date=end_text: client.suspend_d(
                start_date=start_date,
                end_date=end_date,
                fields=",".join(COLUMNS),
            )
        )
        elapsed = time.perf_counter() - started
        logger.debug("suspend_d 返回 %d 行，耗时 %.1f 秒", len(frame), elapsed)
        selected = frame[list(COLUMNS)]
        ordered = selected.sort_values("trade_date")
        rows = ordered.astype(object).where(ordered.notna(), None)
        written += insert_rows(
            connection,
            table="suspend_d",
            columns=COLUMNS,
            rows=rows.itertuples(index=False, name=None),
        )
        chunk_start = chunk_end + timedelta(days=1)

    return written
