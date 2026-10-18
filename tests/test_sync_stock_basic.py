import shutil
import unittest
from pathlib import Path

import pandas as pd

from screener.store import open_database
from screener.sync.stock_basic import COLUMNS, fetch_stock_basic


class FakeClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def stock_basic(self, **params):
        self.calls.append(params)
        return self.frame


def row(**overrides):
    """一行 15 列、默认全是空串的股票，只覆盖关心的字段。"""
    return {**dict.fromkeys(COLUMNS, ""), **overrides}


def frame_of(rows):
    return pd.DataFrame(rows, columns=list(COLUMNS))


class StockBasicTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_fetches_listed_stocks_in_a_single_call_with_explicit_fields(self):
        client = FakeClient(frame_of([row(ts_code="000001.SZ")]))

        fetch_stock_basic(self.connection, client)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["list_status"], "L")
        self.assertEqual(client.calls[0]["exchange"], "")
        self.assertEqual(client.calls[0]["fields"], ",".join(COLUMNS))

    def test_writes_the_rows_and_returns_the_count(self):
        rows = [
            row(ts_code="000001.SZ", name="平安银行", list_status="L"),
            row(ts_code="600000.SH", name="浦发银行", list_status="L"),
        ]

        count = fetch_stock_basic(self.connection, FakeClient(frame_of(rows)))

        self.assertEqual(count, 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT ts_code, name FROM stock_basic ORDER BY ts_code"
            ).fetchall(),
            [("000001.SZ", "平安银行"), ("600000.SH", "浦发银行")],
        )

    def test_a_second_run_replaces_the_previous_rows(self):
        fetch_stock_basic(self.connection, FakeClient(frame_of([row(ts_code="000001.SZ")])))
        fetch_stock_basic(
            self.connection,
            FakeClient(frame_of([row(ts_code="000001.SZ"), row(ts_code="600000.SH")])),
        )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM stock_basic").fetchone(), (2,)
        )

    def test_missing_values_become_null_not_the_string_nan(self):
        rows = [row(ts_code="000001.SZ", delist_date=float("nan"))]

        fetch_stock_basic(self.connection, FakeClient(frame_of(rows)))

        self.assertEqual(
            self.connection.execute("SELECT delist_date FROM stock_basic").fetchone(), (None,)
        )
