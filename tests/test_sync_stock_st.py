import shutil
import unittest
from pathlib import Path

from screener.store import open_database
from screener.sync.stock_st import COLUMNS, PAGE_LIMIT, fetch_stock_st
from tests.tushare_fakes import FakeTushareClient, stock_st_row

TODAY = "20261016"


class StockStTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _rows(self):
        return self.connection.execute(
            "SELECT ts_code, name, trade_date FROM stock_st ORDER BY ts_code"
        ).fetchall()

    def test_writes_the_rows_and_returns_the_count(self):
        rows = [
            stock_st_row(ts_code="000010.SZ", name="*ST美丽", trade_date=TODAY),
            stock_st_row(ts_code="300313.SZ", name="*ST天山", trade_date=TODAY),
        ]

        count = fetch_stock_st(self.connection, FakeTushareClient(stock_st_rows=rows), TODAY)

        self.assertEqual(count, 2)
        self.assertEqual(
            self._rows(), [("000010.SZ", "*ST美丽", TODAY), ("300313.SZ", "*ST天山", TODAY)]
        )

    def test_the_request_names_the_day_and_the_columns(self):
        client = FakeTushareClient()

        fetch_stock_st(self.connection, client, TODAY)

        self.assertEqual(len(client.calls), 1)
        api_name, params = client.calls[0]
        self.assertEqual(api_name, "stock_st")
        self.assertEqual(params["trade_date"], TODAY)
        self.assertEqual(set(params["fields"].split(",")), set(COLUMNS))

    def test_a_second_run_replaces_the_previous_list(self):
        fetch_stock_st(
            self.connection,
            FakeTushareClient(stock_st_rows=[stock_st_row(ts_code="000010.SZ", trade_date=TODAY)]),
            TODAY,
        )
        fetch_stock_st(
            self.connection,
            FakeTushareClient(stock_st_rows=[stock_st_row(ts_code="300313.SZ", trade_date=TODAY)]),
            TODAY,
        )

        self.assertEqual([row[0] for row in self._rows()], ["300313.SZ"])

    def test_an_empty_answer_clears_the_table(self):
        fetch_stock_st(
            self.connection,
            FakeTushareClient(stock_st_rows=[stock_st_row(ts_code="000010.SZ", trade_date=TODAY)]),
            TODAY,
        )

        count = fetch_stock_st(self.connection, FakeTushareClient(), TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(self._rows(), [])

    def test_hitting_the_single_call_limit_is_an_error_not_a_silent_truncation(self):
        rows = [
            stock_st_row(ts_code=f"{index:06d}.SZ", trade_date=TODAY) for index in range(PAGE_LIMIT)
        ]

        with self.assertRaises(RuntimeError):
            fetch_stock_st(self.connection, FakeTushareClient(stock_st_rows=rows), TODAY)


if __name__ == "__main__":
    unittest.main()
