import logging
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


class SyncBatch:
    def __init__(self, connection: sqlite3.Connection, batch_id: int):
        self._connection = connection
        self._batch_id = batch_id

    def record(self, dataset: str, row_count: int) -> None:
        self._connection.execute(
            "INSERT INTO sync_dataset (batch_id, dataset, row_count) VALUES (?, ?, ?)",
            (self._batch_id, dataset, row_count),
        )

    def publish(self, finished_at: str) -> None:
        self._connection.execute(
            "UPDATE sync_batch SET state = 'published', finished_at = ? WHERE batch_id = ?",
            (finished_at, self._batch_id),
        )

    def abort(self) -> None:
        self._connection.execute(
            "UPDATE sync_batch SET state = 'aborted' WHERE batch_id = ?",
            (self._batch_id,),
        )


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # autocommit：不引入事务。中断只留下「已写前缀」，重跑一遍覆盖掉。
    connection = sqlite3.connect(path, isolation_level=None)
    # autocommit 是每行一次提交，默认 synchronous=FULL 会让每次提交都 fsync：
    # 写 8314 行要 66.8 秒，加这两行是 0.27 秒（2026-10-18 实测）。
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_batch (
            batch_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_dataset (
            batch_id INTEGER NOT NULL,
            dataset TEXT NOT NULL,
            row_count INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_basic (
            ts_code TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            area TEXT,
            industry TEXT,
            fullname TEXT,
            enname TEXT,
            cnspell TEXT,
            market TEXT,
            exchange TEXT,
            curr_type TEXT,
            list_status TEXT,
            list_date TEXT,
            delist_date TEXT,
            is_hs TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS stock_basic_list_status ON stock_basic (list_status)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_cal (
            exchange TEXT NOT NULL,
            cal_date TEXT NOT NULL,
            is_open INTEGER,
            pretrade_date TEXT,
            PRIMARY KEY (exchange, cal_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS suspend_d (
            ts_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            suspend_timing TEXT,
            suspend_type TEXT NOT NULL,
            PRIMARY KEY (ts_code, trade_date, suspend_type)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS suspend_d_trade_date ON suspend_d (trade_date)")
    return connection


def insert_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> int:
    """只插入，不删任何东西；连接是 autocommit，写完即落盘。"""
    materialized = list(rows)
    placeholders = ", ".join("?" * len(columns))
    connection.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        materialized,
    )
    return len(materialized)


def delete_rows(connection: sqlite3.Connection, *, table: str, column: str, value: object) -> int:
    """删掉 column = value 的行；连接是 autocommit，删完即落盘。"""
    cursor = connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (value,))
    logger.debug("%s 删除 %s = %s 的行，共 %d 行", table, column, value, cursor.rowcount)
    return cursor.rowcount


def replace_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> int:
    """清空整张表再批量插入；连接是 autocommit，写完即落盘。"""
    materialized = list(rows)
    connection.execute(f"DELETE FROM {table}")
    count = insert_rows(connection, table=table, columns=columns, rows=materialized)
    logger.debug("%s 清空旧数据，写入 %d 行", table, count)
    return count


def _latest_published_id(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        """
        SELECT batch_id FROM sync_batch
        WHERE state = 'published'
        ORDER BY batch_id DESC
        LIMIT 1
        """
    ).fetchone()
    return None if row is None else row[0]


def last_successful_sync_at(connection: sqlite3.Connection) -> str | None:
    batch_id = _latest_published_id(connection)
    if batch_id is None:
        return None
    row = connection.execute(
        "SELECT finished_at FROM sync_batch WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    return row[0]


def published_datasets(connection: sqlite3.Connection) -> frozenset[str]:
    """所有成功批次里出现过的数据集。

    每次更新可以只拉一个数据集，所以「就绪」是跨批次的并集：某个数据集只要在任意
    一次成功更新里出现过就算就绪。只看最新批次会让增量更新后的筛股误报「本地数据
    不完整」。
    """
    rows = connection.execute(
        """
        SELECT DISTINCT sync_dataset.dataset
        FROM sync_dataset
        JOIN sync_batch ON sync_batch.batch_id = sync_dataset.batch_id
        WHERE sync_batch.state = 'published'
        """
    ).fetchall()
    return frozenset(row[0] for row in rows)


def begin_sync(connection: sqlite3.Connection) -> SyncBatch:
    cursor = connection.execute("INSERT INTO sync_batch (state) VALUES ('open')")
    return SyncBatch(connection, cursor.lastrowid)
