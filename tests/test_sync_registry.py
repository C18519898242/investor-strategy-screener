import shutil
import unittest
from pathlib import Path

from screener.store import open_database
from screener.sync.errors import SyncError
from screener.sync.registry import fetch_datasets
from tests.tushare_fakes import FakeTushareClient, stock_basic_row

TRADE_CAL_ROWS = [{"exchange": "SSE", "cal_date": "20261016", "is_open": 1, "pretrade_date": ""}]


def fake_client():
    return FakeTushareClient(stock_basic_rows=[stock_basic_row()], trade_cal_rows=TRADE_CAL_ROWS)


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
            fetch_datasets(self.connection, ("不存在的数据",), fake_client())

    def test_dispatches_every_registered_dataset(self):
        counts = fetch_datasets(self.connection, ("股票列表", "交易日历"), fake_client())

        self.assertEqual(counts, {"股票列表": 1, "交易日历": 1})
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM trade_cal").fetchone(), (1,))

    def test_logs_progress_for_every_dataset(self):
        with self.assertLogs("screener.sync.registry", level="INFO") as captured:
            fetch_datasets(self.connection, ("股票列表", "交易日历"), fake_client())

        self.assertEqual(
            captured.output,
            [
                "INFO:screener.sync.registry:开始拉取 股票列表",
                "INFO:screener.sync.registry:股票列表 完成：1 行",
                "INFO:screener.sync.registry:开始拉取 交易日历",
                "INFO:screener.sync.registry:交易日历 完成：1 行",
            ],
        )

    def test_unknown_dataset_logs_nothing_before_raising(self):
        with self.assertNoLogs("screener.sync.registry", level="INFO"):
            with self.assertRaisesRegex(SyncError, "未登记的数据名：不存在的数据"):
                fetch_datasets(self.connection, ("不存在的数据",), fake_client())
