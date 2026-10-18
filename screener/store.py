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
        self._connection.commit()

    def abort(self) -> None:
        self._connection.execute(
            "UPDATE sync_batch SET state = 'aborted' WHERE batch_id = ?",
            (self._batch_id,),
        )
        self._connection.commit()


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
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
    connection.commit()
    return connection


def replace_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> int:
    """清空整张表再批量插入；不提交，提交点仍由同步批次决定。"""
    materialized = list(rows)
    connection.execute(f"DELETE FROM {table}")
    placeholders = ", ".join("?" * len(columns))
    connection.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        materialized,
    )
    logger.debug("%s 清空旧数据，写入 %d 行", table, len(materialized))
    return len(materialized)


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
    batch_id = _latest_published_id(connection)
    if batch_id is None:
        return frozenset()
    rows = connection.execute(
        "SELECT dataset FROM sync_dataset WHERE batch_id = ?",
        (batch_id,),
    ).fetchall()
    return frozenset(row[0] for row in rows)


def begin_sync(connection: sqlite3.Connection) -> SyncBatch:
    cursor = connection.execute("INSERT INTO sync_batch (state) VALUES ('open')")
    connection.commit()
    return SyncBatch(connection, cursor.lastrowid)
