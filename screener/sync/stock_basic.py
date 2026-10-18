import logging
import sqlite3
import time

import pandas as pd

from screener.store import replace_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

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


def _fetch_page(client: object, fields: tuple[str, ...]) -> pd.DataFrame:
    """取一页字段，并在调用前后各留一条 DEBUG。"""
    logger.debug("stock_basic 请求：list_status=L, exchange=, fields=%d 个", len(fields))
    started = time.perf_counter()
    frame = call_with_retry(
        lambda: client.stock_basic(exchange="", list_status="L", fields=",".join(fields))
    )
    elapsed = time.perf_counter() - started
    logger.debug("stock_basic 返回 %d 行，耗时 %.1f 秒", len(frame), elapsed)
    return frame


def fetch_stock_basic(connection: sqlite3.Connection, client: object) -> int:
    """只取在市股票：整表替换。

    15 列超过代理的 `fields` 上限，因此拆成两次调用再按 `ts_code` 合并回来。
    """
    head = COLUMNS[:FIELD_LIMIT]
    tail = (MERGE_KEY, *COLUMNS[FIELD_LIMIT:])
    head_frame = _fetch_page(client, head)
    tail_frame = _fetch_page(client, tail)
    frame = head_frame.merge(tail_frame, on=MERGE_KEY, how="left")
    selected = frame[list(COLUMNS)]
    rows = selected.astype(object).where(selected.notna(), None)
    return replace_rows(
        connection,
        table="stock_basic",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )
