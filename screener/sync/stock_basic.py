import sqlite3

from screener.store import replace_rows
from screener.sync.retry import call_with_retry

DATASET = "股票列表"
COLUMNS: tuple[str, ...] = (
    "ts_code",
    "symbol",
    "name",
    "area",
    "industry",
    "fullname",
    "enname",
    "cnspell",
    "market",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
    "is_hs",
)


def fetch_stock_basic(connection: sqlite3.Connection, client: object) -> int:
    """只取在市股票：一次调用、显式列出字段，整表替换。"""
    frame = call_with_retry(
        lambda: client.stock_basic(exchange="", list_status="L", fields=",".join(COLUMNS))
    )
    selected = frame[list(COLUMNS)]
    rows = selected.astype(object).where(selected.notna(), None)
    return replace_rows(
        connection,
        table="stock_basic",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )
