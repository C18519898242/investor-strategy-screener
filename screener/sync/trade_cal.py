import sqlite3
from datetime import date

from screener.store import replace_rows
from screener.sync.retry import call_with_retry

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
    frame = call_with_retry(
        lambda: client.trade_cal(
            exchange=EXCHANGE,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields=",".join(COLUMNS),
        )
    )
    selected = frame[list(COLUMNS)]
    rows = selected.astype(object).where(selected.notna(), None)
    return replace_rows(
        connection,
        table="trade_cal",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )
