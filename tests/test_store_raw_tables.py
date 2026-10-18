import shutil
import sqlite3
import unittest
from pathlib import Path

from screener.store import delete_rows, insert_rows, open_database, replace_rows

CALENDAR_COLUMNS = ("exchange", "cal_date", "is_open", "pretrade_date")
SUSPEND_D_COLUMNS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")


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
        return [(row[1], row[5]) for row in self.connection.execute(f"PRAGMA table_info({table})")]

    def test_raw_tables_have_the_decided_columns_and_primary_keys(self):
        self.assertEqual(
            [name for name, _ in self._columns("stock_basic")],
            [
                "ts_code",
                "symbol",
                "name",
                "area",
                "industry",
                "fullname",
                "enname",
                "cnspell",
                "market",
                "exchange",
                "curr_type",
                "list_status",
                "list_date",
                "delist_date",
                "is_hs",
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

    def test_suspend_d_table_has_the_decided_columns_and_primary_key(self):
        self.assertEqual(
            [name for name, _ in self._columns("suspend_d")],
            ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
        )
        self.assertEqual([pk for _, pk in self._columns("suspend_d")], [1, 2, 0, 3])

    def test_suspend_d_has_a_trade_date_index(self):
        names = [row[1] for row in self.connection.execute("PRAGMA index_list('suspend_d')")]

        self.assertIn("suspend_d_trade_date", names)

    def test_suspend_d_stores_both_types_on_the_same_day(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=SUSPEND_D_COLUMNS,
            rows=[
                ("000001.SZ", "20261016", "09:30-10:00", "S"),
                ("000001.SZ", "20261016", None, "R"),
            ],
        )

        self.assertEqual(
            self.connection.execute(
                "SELECT suspend_timing, suspend_type FROM suspend_d ORDER BY suspend_type"
            ).fetchall(),
            [(None, "R"), ("09:30-10:00", "S")],
        )

    def test_delete_rows_removes_only_the_named_day(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=SUSPEND_D_COLUMNS,
            rows=[
                ("000001.SZ", "20261015", None, "S"),
                ("000001.SZ", "20261016", None, "S"),
                ("000002.SZ", "20261016", None, "S"),
            ],
        )

        removed = delete_rows(
            self.connection, table="suspend_d", column="trade_date", value="20261016"
        )

        self.assertEqual(removed, 2)
        self.assertEqual(
            self.connection.execute("SELECT ts_code, trade_date FROM suspend_d").fetchall(),
            [("000001.SZ", "20261015")],
        )

    def test_insert_rows_is_repeatable_on_an_empty_day(self):
        rows = [("000001.SZ", "20261016", None, "S")]
        insert_rows(self.connection, table="suspend_d", columns=SUSPEND_D_COLUMNS, rows=rows)
        first = self.connection.execute("SELECT * FROM suspend_d").fetchall()
        delete_rows(self.connection, table="suspend_d", column="trade_date", value="20261016")
        insert_rows(self.connection, table="suspend_d", columns=SUSPEND_D_COLUMNS, rows=rows)

        self.assertEqual(self.connection.execute("SELECT * FROM suspend_d").fetchall(), first)

    def test_a_plain_write_is_on_disk_immediately(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=SUSPEND_D_COLUMNS,
            rows=[("000001.SZ", "20261016", None, "S")],
        )

        self.assertFalse(self.connection.in_transaction)
        reopened = sqlite3.connect(self.directory / "screener.sqlite3")
        try:
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM suspend_d").fetchone(), (1,))
        finally:
            reopened.close()
