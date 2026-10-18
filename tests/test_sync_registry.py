import shutil
import unittest
from pathlib import Path

import pandas as pd

from screener.store import open_database
from screener.sync.errors import SyncError
from screener.sync.registry import fetch_datasets
from screener.sync.stock_basic import COLUMNS as STOCK_BASIC_COLUMNS
from screener.sync.trade_cal import COLUMNS as TRADE_CAL_COLUMNS


class FakeClient:
    def stock_basic(self, **params):
        return pd.DataFrame(
            [dict.fromkeys(STOCK_BASIC_COLUMNS, "")], columns=list(STOCK_BASIC_COLUMNS)
        )

    def trade_cal(self, **params):
        return pd.DataFrame(
            [{"exchange": "SSE", "cal_date": "20261016", "is_open": 1, "pretrade_date": ""}],
            columns=list(TRADE_CAL_COLUMNS),
        )


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_unknown_dataset_name_raises(self):
        with self.assertRaisesRegex(SyncError, "未登记的数据名：不存在的数据"):
            fetch_datasets(self.connection, ("不存在的数据",), FakeClient())

    def test_dispatches_every_registered_dataset(self):
        counts = fetch_datasets(self.connection, ("股票列表", "交易日历"), FakeClient())

        self.assertEqual(counts, {"股票列表": 1, "交易日历": 1})
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM trade_cal").fetchone(), (1,)
        )
