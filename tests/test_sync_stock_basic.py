import shutil
import unittest
from pathlib import Path

from screener.store import open_database
from screener.sync.stock_basic import COLUMNS, fetch_stock_basic
from tests.tushare_fakes import STOCK_BASIC_FIELD_LIMIT, FakeTushareClient, stock_basic_row


class StockBasicTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

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
        for _, params in client.calls:
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

    def test_logs_request_and_response_for_both_calls(self):
        client = FakeTushareClient(stock_basic_rows=[stock_basic_row(ts_code="000001.SZ")])

        with self.assertLogs("screener.sync.stock_basic", level="DEBUG") as captured:
            fetch_stock_basic(self.connection, client)

        requests = [line for line in captured.output if "请求" in line]
        responses = [line for line in captured.output if "返回" in line]

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(responses), 2)
        self.assertIn("fields=13 个", requests[0])
        self.assertIn("fields=3 个", requests[1])
        self.assertTrue(all("耗时" in line for line in responses))
