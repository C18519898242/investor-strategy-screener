import logging
import sqlite3
import time
from datetime import date

from screener.store import delete_rows, insert_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "每日指标"
COLUMNS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)


def _is_open_day(connection: sqlite3.Connection, trade_date: str, end: str) -> bool:
    """日历确认这天是交易日、且不晚于 end。

    只有这种水位删掉之后才会回到待补集合：待补集合是从日历算出来的，日历为空
    或不完整、水位又是上游返回的未来日期时，删掉它就是白丢一天的数据。
    """
    row = connection.execute(
        "SELECT 1 FROM trade_cal WHERE is_open = 1 AND cal_date = ? AND cal_date <= ?",
        (trade_date, end),
    ).fetchone()
    return row is not None


def fetch_daily_basic(
    connection: sqlite3.Connection, client: object, *, today: date | None = None
) -> int:
    """按交易日历补齐 daily_basic：先删水位那天，再逐日补日历里还没有行的交易日。

    待补集合是「日历里的交易日」减去「库里已有行的日期」，所以水位以下没有行的
    交易日也会补上，中间的空洞不会永久留下。日历为空时什么都不拉，也不报错。

    水位那天只有在日历确认它是交易日时才删（照 `suspend_d` 的守卫）：否则删掉它
    等于白丢一天，日历算不出它，这一轮也不会重取。
    """
    end = (today or date.today()).strftime("%Y%m%d")
    water = connection.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
    if water is not None:
        if _is_open_day(connection, water, end):
            logger.debug("daily_basic 水位 %s，先删掉那天的旧行", water)
            delete_rows(connection, table="daily_basic", column="trade_date", value=water)
        else:
            logger.debug("daily_basic 水位 %s 不在日历里或晚于 %s，不删也不补", water, end)

    days = [
        row[0]
        for row in connection.execute(
            """
            SELECT cal_date FROM trade_cal
            WHERE is_open = 1 AND cal_date <= ?
              AND cal_date NOT IN (SELECT trade_date FROM daily_basic)
            ORDER BY cal_date
            """,
            (end,),
        )
    ]

    written = 0
    total = len(days)
    for index, trade_date in enumerate(days, start=1):
        logger.info("每日指标 第 %d/%d 个交易日：%s", index, total, trade_date)
        logger.debug("daily_basic 请求：%s", trade_date)
        started = time.perf_counter()
        frame = call_with_retry(
            lambda trade_date=trade_date: client.daily_basic(
                trade_date=trade_date,
                fields=",".join(COLUMNS),
            )
        )
        elapsed = time.perf_counter() - started
        logger.debug("daily_basic 返回 %d 行，耗时 %.1f 秒", len(frame), elapsed)
        selected = frame[list(COLUMNS)]
        rows = selected.astype(object).where(selected.notna(), None)
        written += insert_rows(
            connection,
            table="daily_basic",
            columns=COLUMNS,
            rows=rows.itertuples(index=False, name=None),
        )

    return written
