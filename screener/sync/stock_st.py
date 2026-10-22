"""ST 股票名单：只取最近一个交易日的全量，用来把 `stock_basic` 里的风险警示股摘掉。

`stock_basic` 本身没有「是不是 ST」这个字段（`list_status` 只有 L/D/P），只能看名称
前缀，而名称又是当前值。所以 ST 名单单独从 `stock_st` 接口取——它支持按交易日取
历史，是唯一准确的来源。实测 20261016 返回 201 只，与 `stock_basic` 名称带 ST 的
201 只完全一致。
"""

import logging
import sqlite3

import pandas as pd

from screener.store import replace_rows
from screener.sync.retry import call_with_retry

logger = logging.getLogger(__name__)

DATASET = "ST 股票"
COLUMNS: tuple[str, ...] = ("ts_code", "name", "trade_date", "type", "type_name")
# 接口单次上限 1,000 行（实测一天 201 行）。真撞上上限说明假设变了，宁可报错也不要静默截断。
PAGE_LIMIT = 1000


def fetch_stock_st(connection: sqlite3.Connection, client: object, trade_date: str) -> int:
    """用某个交易日的全量替换本地 ST 名单，返回行数。"""
    logger.debug("stock_st 请求：trade_date=%s", trade_date)
    frame = call_with_retry(
        lambda: client.stock_st(trade_date=trade_date, fields=",".join(COLUMNS))
    )
    if len(frame) >= PAGE_LIMIT:
        raise RuntimeError(
            f"stock_st {trade_date} 返回 {len(frame)} 行，碰到 {PAGE_LIMIT} 行的单次上限，"
            "需要加分页再继续"
        )
    selected = frame[list(COLUMNS)]
    rows = selected.astype(object).where(pd.notna(selected), None)
    count = replace_rows(
        connection,
        table="stock_st",
        columns=COLUMNS,
        rows=rows.itertuples(index=False, name=None),
    )
    logger.debug("stock_st 返回 %d 行", count)
    return count
