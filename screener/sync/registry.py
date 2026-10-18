import logging
import sqlite3
from collections.abc import Callable, Mapping

from screener.sync.errors import SyncError
from screener.sync.stock_basic import DATASET as STOCK_BASIC_DATASET
from screener.sync.stock_basic import fetch_stock_basic
from screener.sync.suspend_d import DATASET as SUSPEND_D_DATASET
from screener.sync.suspend_d import fetch_suspend_d
from screener.sync.trade_cal import DATASET as TRADE_CAL_DATASET
from screener.sync.trade_cal import fetch_trade_cal

logger = logging.getLogger(__name__)

DATASET_FETCHERS: dict[str, Callable[[sqlite3.Connection, object], int]] = {
    STOCK_BASIC_DATASET: fetch_stock_basic,
    TRADE_CAL_DATASET: fetch_trade_cal,
    SUSPEND_D_DATASET: fetch_suspend_d,
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
        logger.info("开始拉取 %s", dataset)
        counts[dataset] = fetcher(connection, client)
        logger.info("%s 完成：%d 行", dataset, counts[dataset])
    return counts
