import logging
import sqlite3
import time
from datetime import date

import pandas as pd

from screener.store import replace_rows
from screener.sync.retry import call_with_retry
from screener.sync.stock_st import fetch_stock_st

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


def _replace_basic(connection: sqlite3.Connection, client: object) -> int:
    """15 列超过代理的 `fields` 上限，拆成两次调用再按 `ts_code` 合并回来。"""
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


def latest_open_day(connection: sqlite3.Connection, today: date | None = None) -> str:
    """最近一个交易日（含今天），trade_cal 没数据就退回今天。"""
    day = (today or date.today()).strftime("%Y%m%d")
    row = connection.execute(
        """
        SELECT MAX(cal_date) FROM trade_cal
        WHERE exchange = 'SSE' AND is_open = 1 AND cal_date <= ?
        """,
        (day,),
    ).fetchone()
    return row[0] if row and row[0] else day


def drop_st(connection: sqlite3.Connection) -> int:
    """按代码把 `stock_st` 里的股票从 `stock_basic` 删掉，返回删掉的行数。"""
    cursor = connection.execute(
        "DELETE FROM stock_basic WHERE ts_code IN (SELECT ts_code FROM stock_st)"
    )
    return cursor.rowcount


def fetch_stock_basic(connection: sqlite3.Connection, client: object) -> int:
    """整表替换在市股票，再把 ST 名单取回来从 `stock_basic` 里摘掉。

    `stock_basic` 没有 ST 字段，名称又是当前值，所以拆成两个接口：先落全量，再按
    `stock_st` 当天的名单删。删完表里只剩非 ST 的股票，下游筛选不用再判名称。
    """
    written = _replace_basic(connection, client)
    st_count = fetch_stock_st(connection, client, latest_open_day(connection))
    removed = drop_st(connection)
    logger.info(
        "%s 写入 %d 行，ST 名单 %d 只，剔除 %d 行，剩 %d 行",
        DATASET,
        written,
        st_count,
        removed,
        written - removed,
    )
    return written - removed
