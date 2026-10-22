import shutil
import unittest
from datetime import date
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.sync.stock_basic import COLUMNS, drop_st, fetch_stock_basic, latest_open_day
from tests.tushare_fakes import (
    STOCK_BASIC_FIELD_LIMIT,
    FakeTushareClient,
    stock_basic_row,
    stock_st_row,
)


class StockBasicTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _codes(self):
        return [row[0] for row in self.connection.execute("SELECT ts_code FROM stock_basic")]

    def test_splits_the_field_list_to_stay_within_the_proxy_limit(self):
        client = FakeTushareClient(stock_basic_rows=[stock_basic_row(ts_code="000001.SZ")])

        fetch_stock_basic(self.connection, client)

        sent = [
            params["fields"].split(",")
            for api_name, params in client.calls
            if api_name == "stock_basic"
        ]
        self.assertEqual(len(sent), 2)
        for fields in sent:
            self.assertLessEqual(len(fields), STOCK_BASIC_FIELD_LIMIT)
        sent_sets = [set(fields) for fields in sent]
        self.assertEqual(set().union(*sent_sets), set(COLUMNS))
        self.assertEqual(set.intersection(*sent_sets), {"ts_code"})
        for api_name, params in client.calls:
            if api_name != "stock_basic":
                continue
            self.assertEqual(params["list_status"], "L")
            self.assertEqual(params["exchange"], "")

    def test_writes_the_rows_and_returns_the_count(self):
        rows = [
            stock_basic_row(ts_code="000001.SZ", name="平安银行", list_status="L", is_hs="N"),
            stock_basic_row(ts_code="600000.SH", name="浦发银行", list_status="L", is_hs="N"),
        ]

        count = fetch_stock_basic(self.connection, FakeTushareClient(stock_basic_rows=rows))

        self.assertEqual(count, 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT ts_code, name, list_status, is_hs FROM stock_basic ORDER BY ts_code"
            ).fetchall(),
            [("000001.SZ", "平安银行", "L", "N"), ("600000.SH", "浦发银行", "L", "N")],
        )

    def test_a_second_run_replaces_the_previous_rows(self):
        fetch_stock_basic(
            self.connection,
            FakeTushareClient(stock_basic_rows=[stock_basic_row(ts_code="000001.SZ")]),
        )
        fetch_stock_basic(
            self.connection,
            FakeTushareClient(
                stock_basic_rows=[
                    stock_basic_row(ts_code="000001.SZ"),
                    stock_basic_row(ts_code="600000.SH"),
                ]
            ),
        )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM stock_basic").fetchone(), (2,)
        )

    def test_missing_values_become_null_not_the_string_nan(self):
        rows = [stock_basic_row(ts_code="000001.SZ", delist_date=float("nan"))]

        fetch_stock_basic(self.connection, FakeTushareClient(stock_basic_rows=rows))

        self.assertEqual(
            self.connection.execute("SELECT delist_date FROM stock_basic").fetchone(), (None,)
        )

    def test_st_stocks_are_deleted_by_code_after_the_full_replace(self):
        """stock_basic 没有 ST 字段，只能整表替换后按 stock_st 的代码删。"""
        day = latest_open_day(self.connection)
        client = FakeTushareClient(
            stock_basic_rows=[
                stock_basic_row(ts_code="000001.SZ", name="平安银行"),
                stock_basic_row(ts_code="000010.SZ", name="*ST美丽"),
            ],
            stock_st_rows=[stock_st_row(ts_code="000010.SZ", name="*ST美丽", trade_date=day)],
        )

        count = fetch_stock_basic(self.connection, client)

        self.assertEqual(count, 1)
        self.assertEqual(self._codes(), ["000001.SZ"])

    def test_the_returned_count_is_what_is_left(self):
        day = latest_open_day(self.connection)
        client = FakeTushareClient(
            stock_basic_rows=[
                stock_basic_row(ts_code="000001.SZ"),
                stock_basic_row(ts_code="000002.SZ"),
                stock_basic_row(ts_code="000010.SZ"),
            ],
            stock_st_rows=[stock_st_row(ts_code="000010.SZ", trade_date=day)],
        )

        self.assertEqual(fetch_stock_basic(self.connection, client), 2)

    def test_a_name_change_alone_does_not_remove_a_stock(self):
        """删除只看代码，不看名称——名称里带 ST 但不是 stock_st 名单里的不动。"""
        client = FakeTushareClient(
            stock_basic_rows=[stock_basic_row(ts_code="000001.SZ", name="ST某股")],
            stock_st_rows=[stock_st_row(ts_code="000010.SZ", trade_date="20261016")],
        )

        fetch_stock_basic(self.connection, client)

        self.assertEqual(self._codes(), ["000001.SZ"])

    def test_the_st_list_is_requested_for_the_latest_trading_day(self):
        insert_rows(
            self.connection,
            table="trade_cal",
            columns=("exchange", "cal_date", "is_open"),
            rows=[("SSE", "20260930", 1), ("SSE", "20261001", 0)],
        )
        client = FakeTushareClient(stock_basic_rows=[stock_basic_row(ts_code="000001.SZ")])

        fetch_stock_basic(self.connection, client)

        sent = [params for api_name, params in client.calls if api_name == "stock_st"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["trade_date"], latest_open_day(self.connection, date(2026, 10, 2)))

    def test_latest_open_day_skips_the_closed_days(self):

        insert_rows(
            self.connection,
            table="trade_cal",
            columns=("exchange", "cal_date", "is_open"),
            rows=[("SSE", "20260930", 1), ("SSE", "20261001", 0), ("SSE", "20261002", 1)],
        )

        self.assertEqual(latest_open_day(self.connection, date(2026, 10, 2)), "20261002")
        self.assertEqual(latest_open_day(self.connection, date(2026, 10, 1)), "20260930")

    def test_latest_open_day_falls_back_to_today_without_a_calendar(self):

        self.assertEqual(latest_open_day(self.connection, date(2026, 10, 2)), "20261002")

    def test_drop_st_returns_the_number_of_removed_rows(self):
        insert_rows(
            self.connection,
            table="stock_basic",
            columns=("ts_code", "name"),
            rows=[("000001.SZ", "平安银行"), ("000010.SZ", "*ST美丽")],
        )
        insert_rows(
            self.connection,
            table="stock_st",
            columns=("ts_code", "trade_date"),
            rows=[("000010.SZ", "20261016")],
        )

        self.assertEqual(drop_st(self.connection), 1)
        self.assertEqual(self._codes(), ["000001.SZ"])

    def test_logs_request_and_response_for_both_calls(self):
        client = FakeTushareClient(stock_basic_rows=[stock_basic_row(ts_code="000001.SZ")])

        with self.assertLogs("screener.sync.stock_basic", level="DEBUG") as captured:
            fetch_stock_basic(self.connection, client)

        requests = [line for line in captured.output if "stock_basic 请求" in line]
        responses = [line for line in captured.output if "stock_basic 返回" in line]

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(responses), 2)
        self.assertIn("fields=13 个", requests[0])
        self.assertIn("fields=3 个", requests[1])
        self.assertTrue(all("耗时" in line for line in responses))


if __name__ == "__main__":
    unittest.main()
