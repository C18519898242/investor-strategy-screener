import logging
import sqlite3
from collections.abc import Callable, Mapping

from screener.sync.daily_basic import DATASET as DAILY_BASIC_DATASET
from screener.sync.daily_basic import fetch_daily_basic
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
    DAILY_BASIC_DATASET: fetch_daily_basic,
}

# 每个数据集的水位查询；整表替换的数据集写 None。键必须与 DATASET_FETCHERS 一致。
WATER_QUERIES: dict[str, str | None] = {
    STOCK_BASIC_DATASET: None,
    TRADE_CAL_DATASET: "SELECT MAX(cal_date) FROM trade_cal",
    SUSPEND_D_DATASET: "SELECT MAX(trade_date) FROM suspend_d",
    DAILY_BASIC_DATASET: "SELECT MAX(trade_date) FROM daily_basic",
}


def registered_datasets() -> tuple[str, ...]:
    """更新菜单列的数据集：登记表里的全部数据集。

    与策略声明无关 —— 菜单回答的是「这个程序能拉什么」，不是「当前策略要什么」。
    """
    return tuple(DATASET_FETCHERS)


def dataset_water_levels(
    connection: sqlite3.Connection, datasets: tuple[str, ...]
) -> Mapping[str, str]:
    """给每个数据集一句人话的水位，供更新菜单显示。

    未登记的数据名照实说，不在这里报错——报错留给真正抓取时的 `fetch_datasets`。
    """
    levels = {}
    for dataset in datasets:
        query = WATER_QUERIES.get(dataset)
        if dataset not in DATASET_FETCHERS:
            levels[dataset] = "未登记"
        elif query is None:
            levels[dataset] = "整表替换"
        else:
            water = connection.execute(query).fetchone()[0]
            levels[dataset] = "无数据，首次全量" if water is None else f"水位 {water}"
    return levels


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
