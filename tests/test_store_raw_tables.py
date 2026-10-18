import shutil
import unittest
from pathlib import Path

from screener.store import open_database, replace_rows

CALENDAR_COLUMNS = ("exchange", "cal_date", "is_open", "pretrade_date")


class RawTablesTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _columns(self, table):
        return [
            (row[1], row[5])
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        ]

    def test_raw_tables_have_the_decided_columns_and_primary_keys(self):
        self.assertEqual(
            [name for name, _ in self._columns("stock_basic")],
            [
                "ts_code", "symbol", "name", "area", "industry", "fullname", "enname",
                "cnspell", "market", "exchange", "curr_type", "list_status", "list_date",
                "delist_date", "is_hs",
            ],
        )
        self.assertEqual([pk for _, pk in self._columns("stock_basic")], [1] + [0] * 14)
        self.assertEqual(
            [name for name, _ in self._columns("trade_cal")],
            ["exchange", "cal_date", "is_open", "pretrade_date"],
        )
        self.assertEqual([pk for _, pk in self._columns("trade_cal")], [1, 2, 0, 0])

    def test_replace_rows_keeps_only_the_rows_from_this_call(self):
        replace_rows(
            self.connection,
            table="trade_cal",
            columns=CALENDAR_COLUMNS,
            rows=[("SSE", "20261016", 1, "20261015"), ("SSE", "20261017", 0, "20261016")],
        )
        count = replace_rows(
            self.connection,
            table="trade_cal",
            columns=CALENDAR_COLUMNS,
            rows=[("SSE", "20261018", 0, "20261016")],
        )

        self.assertEqual(count, 1)
        self.assertEqual(
            self.connection.execute("SELECT cal_date FROM trade_cal").fetchall(),
            [("20261018",)],
        )

    def test_replace_rows_is_repeatable(self):
        rows = [("SSE", "20261016", 1, "20261015")]
        replace_rows(self.connection, table="trade_cal", columns=CALENDAR_COLUMNS, rows=rows)
        first = self.connection.execute("SELECT * FROM trade_cal").fetchall()
        replace_rows(self.connection, table="trade_cal", columns=CALENDAR_COLUMNS, rows=rows)

        self.assertEqual(self.connection.execute("SELECT * FROM trade_cal").fetchall(), first)

    def test_replace_rows_stores_empty_string_and_none_as_given(self):
        replace_rows(
            self.connection,
            table="trade_cal",
            columns=CALENDAR_COLUMNS,
            rows=[("SSE", "20150105", 1, "")],
        )
        replace_rows(
            self.connection,
            table="stock_basic",
            columns=("ts_code", "delist_date"),
            rows=[("000001.SZ", None)],
        )

        self.assertEqual(
            self.connection.execute("SELECT pretrade_date FROM trade_cal").fetchone(), ("",)
        )
        self.assertEqual(
            self.connection.execute("SELECT delist_date FROM stock_basic").fetchone(), (None,)
        )
