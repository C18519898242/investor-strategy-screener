import shutil
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from screener.store import insert_rows, open_database
from screener.sync import cashflow as module
from screener.sync.cashflow import COLUMNS, REPORT_TYPE, fetch_cashflow
from screener.sync.errors import SyncError
from screener.sync.registry import DATASET_FETCHERS, WATER_QUERIES
from tests.tushare_fakes import cashflow_row

TODAY = date(2026, 11, 1)


def statement(ts_code="000001.SZ", period="20260930", **overrides):
    """一行单季现金流量表，只填几个关键字段，其余靠 cashflow_row 兜成 None。"""
    defaults = {
        "ts_code": ts_code,
        "end_date": period,
        "report_type": REPORT_TYPE,
        "ann_date": "20261020",
        "update_flag": "0",
        "n_cashflow_act": 1000.0,
        "c_pay_acq_const_fiolta": 200.0,
    }
    return cashflow_row(**{**defaults, **overrides})


def values(ts_code="000001.SZ", period="20260930", **overrides):
    row = statement(ts_code, period, **overrides)
    return tuple(row[name] for name in COLUMNS)


class FakeFeed:
    """替掉 `_fetch_period`：按请求记录调用，返回预先排好的行。"""

    def __init__(self, rows=(), error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []

    def __call__(self, ts_code, period):
        self.calls.append((ts_code, period))
        if self.error is not None:
            raise self.error
        return [row for row in self.rows if row["ts_code"] == ts_code and row["end_date"] == period]


class CashflowTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")
        insert_rows(
            self.connection,
            table="stock_basic",
            columns=("ts_code",),
            rows=[("000001.SZ",), ("000002.SZ",)],
        )

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _run(self, feed, today=TODAY):
        with mock.patch.object(module, "_fetch_period", feed):
            return fetch_cashflow(self.connection, today=today)

    def _stored(self):
        return [
            (row[0], row[1], row[2], row[3])
            for row in self.connection.execute(
                "SELECT ts_code, end_date, report_type, n_cashflow_act"
                " FROM cashflow ORDER BY ts_code"
            )
        ]

    def test_every_missing_stock_is_requested_and_stored(self):
        feed = FakeFeed([statement("000001.SZ"), statement("000002.SZ")])

        count = self._run(feed)

        self.assertEqual(feed.calls, [("000001.SZ", "20260930"), ("000002.SZ", "20260930")])
        self.assertEqual(count, 2)
        self.assertEqual(
            self._stored(),
            [
                ("000001.SZ", "20260930", "2", 1000.0),
                ("000002.SZ", "20260930", "2", 1000.0),
            ],
        )

    def test_a_second_run_makes_no_request(self):
        self._run(FakeFeed([statement("000001.SZ"), statement("000002.SZ")]))
        feed = FakeFeed()

        count = self._run(feed)

        self.assertEqual(feed.calls, [])
        self.assertEqual(count, 0)

    def test_stocks_that_already_have_the_period_are_not_requested(self):
        insert_rows(self.connection, table="cashflow", columns=COLUMNS, rows=[values("000001.SZ")])
        feed = FakeFeed([statement("000002.SZ")])

        self._run(feed)

        self.assertEqual(feed.calls, [("000002.SZ", "20260930")])

    def test_a_cumulative_row_does_not_count_as_the_single_quarter_row(self):
        insert_rows(
            self.connection,
            table="cashflow",
            columns=COLUMNS,
            rows=[values("000001.SZ", report_type="1")],
        )
        feed = FakeFeed([statement("000001.SZ")])

        self._run(feed)

        self.assertIn(("000001.SZ", "20260930"), feed.calls)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM cashflow WHERE report_type = ?", (REPORT_TYPE,)
            ).fetchone(),
            (1,),
        )

    def test_the_capex_and_operating_cash_flow_columns_are_kept(self):
        self._run(FakeFeed([statement("000001.SZ")]))

        self.assertEqual(
            self.connection.execute(
                "SELECT n_cashflow_act, c_pay_acq_const_fiolta FROM cashflow"
            ).fetchone(),
            (1000.0, 200.0),
        )

    def test_a_stock_without_an_answer_is_requested_again_next_run(self):
        first = self._run(FakeFeed([statement("000001.SZ")]))
        feed = FakeFeed()

        second = self._run(feed)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(feed.calls, [("000002.SZ", "20260930")])

    def test_two_revisions_of_the_same_period_are_both_kept(self):
        feed = FakeFeed(
            [
                statement("000001.SZ", update_flag="0"),
                statement("000001.SZ", update_flag="1", ann_date="20261025"),
            ]
        )

        count = self._run(feed)

        self.assertEqual(count, 2)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM cashflow").fetchone()[0], 2)

    def test_duplicate_rows_of_one_revision_are_collapsed_before_writing(self):
        count = self._run(FakeFeed([statement("000001.SZ"), statement("000001.SZ")]))

        self.assertEqual(count, 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM cashflow").fetchone()[0], 1)

    def test_rows_of_another_period_are_dropped(self):
        feed = FakeFeed([statement("000001.SZ", "20260630")])

        count = self._run(feed)

        self.assertEqual(count, 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM cashflow").fetchone()[0], 0)

    def test_null_values_are_stored_as_sql_null(self):
        self._run(FakeFeed([statement("000001.SZ", c_pay_acq_const_fiolta=None)]))

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM cashflow WHERE c_pay_acq_const_fiolta IS NULL"
            ).fetchone(),
            (1,),
        )


class RegistrationTest(unittest.TestCase):
    def test_the_dataset_is_registered_for_incremental_updates(self):
        self.assertIs(DATASET_FETCHERS["现金流量表"], fetch_cashflow)
        self.assertEqual(
            WATER_QUERIES["现金流量表"],
            "SELECT MAX(end_date) FROM cashflow WHERE report_type = '2'",
        )


class TransportTest(unittest.TestCase):
    def test_a_rate_limit_answer_becomes_a_sync_error(self):
        payload = b'{"code": 429, "msg": "too many concurrent requests"}'

        with mock.patch.object(
            module.urllib.request, "urlopen", side_effect=self._http_ok(payload)
        ):
            with self.assertRaises(SyncError):
                module._fetch_period("000001.SZ", "20260930")

    def test_a_non_zero_code_becomes_a_sync_error(self):
        payload = b'{"code": 40001, "msg": "invalid token"}'

        with mock.patch.object(
            module.urllib.request, "urlopen", side_effect=self._http_ok(payload)
        ):
            with self.assertRaises(SyncError):
                module._fetch_period("000001.SZ", "20260930")

    @staticmethod
    def _http_ok(payload):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return payload

        return lambda *args, **kwargs: Response()


if __name__ == "__main__":
    unittest.main()
