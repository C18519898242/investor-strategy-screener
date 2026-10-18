import sqlite3
from collections.abc import Callable, Mapping

from screener.config import Settings
from screener.store import begin_sync
from screener.sync.client import create_tushare_client


def fetch_not_implemented(datasets: tuple[str, ...], token: str) -> Mapping[str, int]:
    raise NotImplementedError("Tushare 同步尚未实现")


def sync_all(
    connection: sqlite3.Connection,
    *,
    datasets: tuple[str, ...],
    settings: Settings,
    fetch: Callable[[sqlite3.Connection, tuple[str, ...], object], Mapping[str, int]],
    finished_at: str,
) -> str:
    client = create_tushare_client(settings)
    batch = begin_sync(connection)
    try:
        counts = fetch(connection, datasets, client)
        missing = [dataset for dataset in datasets if dataset not in counts]
        if missing:
            raise RuntimeError("同步结果缺少所需数据")
        for dataset in datasets:
            batch.record(dataset, counts[dataset])
        batch.publish(finished_at)
    except Exception:
        batch.abort()
        raise
    return finished_at
