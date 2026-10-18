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


MERGE_KEY = "ts_code"
# 代理对 `stock_basic` 的 `fields` 上限（2026-10-18 实测）：13 个字段（字符串长度 113）成功，
# 14 个（长度 111）报 `invalid_params` —— 卡的是**个数**不是字符串长度，`fields='*'` 也报错。
FIELD_LIMIT = 13


def fetch_stock_basic(connection: sqlite3.Connection, client: object) -> int:
    """只取在市股票：整表替换。

    15 列超过代理的 `fields` 上限，因此拆成两次调用再按 `ts_code` 合并回来。
    """
    head = COLUMNS[:FIELD_LIMIT]
    tail = (MERGE_KEY, *COLUMNS[FIELD_LIMIT:])
    head_frame = call_with_retry(
        lambda: client.stock_basic(exchange="", list_status="L", fields=",".join(head))
    )
    tail_frame = call_with_retry(
        lambda: client.stock_basic(exchange="", list_status="L", fields=",".join(tail))
    )
    frame = head_frame.merge(tail_frame, on=MERGE_KEY, how="left")
    selected = frame[list(COLUMNS)]
    rows = selected.astype(object).where(selected.notna(), None)
    return replace_rows(
        connection,
        table="stock_basic",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )
