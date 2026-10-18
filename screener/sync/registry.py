import sqlite3
from collections.abc import Callable, Mapping

from screener.sync.errors import SyncError
from screener.sync.stock_basic import DATASET as STOCK_BASIC_DATASET
from screener.sync.stock_basic import fetch_stock_basic
from screener.sync.trade_cal import DATASET as TRADE_CAL_DATASET
from screener.sync.trade_cal import fetch_trade_cal

DATASET_FETCHERS: dict[str, Callable[[sqlite3.Connection, object], int]] = {
    STOCK_BASIC_DATASET: fetch_stock_basic,
    TRADE_CAL_DATASET: fetch_trade_cal,
}


def fetch_datasets(
    connection: sqlite3.Connection, datasets: tuple[str, ...], client: object
) -> Mapping[str, int]:
    """按中文数据名逐个抓取；没登记的数据名直接报错，不静默跳过。"""
    counts = {}
    for dataset in datasets:
        fetcher = DATASET_FETCHERS.get(dataset)
        if fetcher is None:
            raise SyncError(f"未登记的数据名：{dataset}")
        counts[dataset] = fetcher(connection, client)
    return counts
