import logging
import sqlite3
import time
from datetime import date

from screener.store import replace_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "交易日历"
COLUMNS: tuple[str, ...] = ("exchange", "cal_date", "is_open", "pretrade_date")
EXCHANGE = "SSE"
BACK_YEARS = 11


def fetch_trade_cal(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """取运行日往前 11 年的 SSE 日历：一次调用、显式列出字段，整表替换。"""
    end = today or date.today()
    start = end.replace(year=end.year - BACK_YEARS)
    logger.debug(
        "trade_cal 请求：exchange=%s, 起始日期=%s, fields=%d 个",
        EXCHANGE,
        start.strftime("%Y%m%d"),
        len(COLUMNS),
    )
    started = time.perf_counter()
    frame = call_with_retry(
        lambda: client.trade_cal(
            exchange=EXCHANGE,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields=",".join(COLUMNS),
        )
    )
    elapsed = time.perf_counter() - started
    logger.debug("trade_cal 返回 %d 行，耗时 %.1f 秒", len(frame), elapsed)
    selected = frame[list(COLUMNS)]
    rows = selected.astype(object).where(selected.notna(), None)
    return replace_rows(
        connection,
        table="trade_cal",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )
