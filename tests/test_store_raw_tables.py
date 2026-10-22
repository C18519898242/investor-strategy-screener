import shutil
import sqlite3
import unittest
from pathlib import Path

from screener.store import delete_rows, insert_rows, open_database, replace_rows

CALENDAR_COLUMNS = ("exchange", "cal_date", "is_open", "pretrade_date")
SUSPEND_D_COLUMNS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
DAILY_BASIC_COLUMNS = (
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
)


def daily_basic_row(trade_date, **overrides):
    row = dict.fromkeys(DAILY_BASIC_COLUMNS, 0.0)
    row["ts_code"] = "000001.SZ"
    row["trade_date"] = trade_date
    row["close"] = 10.0
    row["pe"] = 8.0
    return {**row, **overrides}


def daily_basic_values(trade_date, **overrides):
    """insert_rows 收的是序列，所以按列顺序把上面那行转成元组。"""
    row = daily_basic_row(trade_date, **overrides)
    return tuple(row[name] for name in DAILY_BASIC_COLUMNS)


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

    def test_stock_st_matches_the_columns_the_sync_module_writes(self):
        """建表语句和 `screener.sync.stock_st.COLUMNS` 都是手写的，顺序错了会错列落库。"""
        from screener.sync.stock_st import COLUMNS as STOCK_ST_COLUMNS

        self.assertEqual(
            [name for name, _ in self._columns("stock_st")],
            list(STOCK_ST_COLUMNS),
        )

    def test_stock_st_keeps_one_row_per_stock_and_day(self):
        primary = sorted((pk, name) for name, pk in self._columns("stock_st") if pk)

        self.assertEqual([name for _, name in primary], ["ts_code", "trade_date"])

    def test_stock_st_has_a_trade_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'stock_st'"
            )
        ]

        self.assertIn("stock_st_trade_date", names)

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

    def test_the_connection_trades_durability_for_speed(self):
        # autocommit 下每行一次提交。没有这两个 pragma 时写 8314 行要 66.8 秒，
        # 有它是 0.27 秒（2026-10-18 实测）—— 回填会慢 250 倍。
        # 代价是提交不 fsync：进程崩溃不丢，断电可能丢最后几笔。
        self.assertEqual(self.connection.execute("PRAGMA journal_mode").fetchone(), ("wal",))
        self.assertEqual(self.connection.execute("PRAGMA synchronous").fetchone(), (1,))

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

    def test_daily_basic_table_has_the_decided_columns_and_primary_key(self):
        self.assertEqual(
            [name for name, _ in self._columns("daily_basic")],
            list(DAILY_BASIC_COLUMNS),
        )
        self.assertEqual([pk for _, pk in self._columns("daily_basic")], [1, 2] + [0] * 16)

    def test_daily_basic_rejects_the_same_stock_on_the_same_day(self):
        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(
                self.connection,
                table="daily_basic",
                columns=DAILY_BASIC_COLUMNS,
                rows=[daily_basic_values("20261016"), daily_basic_values("20261016")],
            )

    def test_daily_basic_stores_a_full_row_and_deletes_only_the_named_day(self):
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=DAILY_BASIC_COLUMNS,
            rows=[
                daily_basic_values("20261015"),
                daily_basic_values("20261016", close=12.5, pe=None),
            ],
        )

        removed = delete_rows(
            self.connection, table="daily_basic", column="trade_date", value="20261016"
        )

        self.assertEqual(removed, 1)
        self.assertEqual(
            self.connection.execute("SELECT trade_date, close, pe FROM daily_basic").fetchall(),
            [("20261015", 10.0, 8.0)],
        )

    def test_daily_basic_has_a_trade_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'daily_basic'"
            )
        ]

        self.assertIn("daily_basic_trade_date", names)

    def test_income_table_matches_the_columns_the_sync_module_writes(self):
        """建表语句和 `screener.sync.income.COLUMNS` 都是手写的，顺序错了会错列落库。"""
        from screener.sync.income import COLUMNS as INCOME_COLUMNS

        self.assertEqual([name for name, _ in self._columns("income")], list(INCOME_COLUMNS))

    def test_income_primary_key_covers_the_whole_revision_identity(self):
        primary = sorted((pk, name) for name, pk in self._columns("income") if pk)

        self.assertEqual(
            [name for _, name in primary],
            ["ts_code", "end_date", "report_type", "ann_date", "update_flag"],
        )

    def test_income_has_an_end_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'income'"
            )
        ]

        self.assertIn("idx_income_end_date", names)

    def test_income_rejects_the_same_revision_twice(self):
        columns = ("ts_code", "ann_date", "end_date", "report_type", "update_flag")
        row = ("000001.SZ", "20261020", "20260930", "2", "0")

        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(
                self.connection,
                table="income",
                columns=columns,
                rows=[row, row],
            )

    def test_income_keeps_two_revisions_of_the_same_period(self):
        columns = ("ts_code", "ann_date", "end_date", "report_type", "update_flag")
        insert_rows(
            self.connection,
            table="income",
            columns=columns,
            rows=[
                ("000001.SZ", "20261020", "20260930", "2", "0"),
                ("000001.SZ", "20261025", "20260930", "2", "1"),
            ],
        )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM income").fetchone(),
            (2,),
        )

    def test_balancesheet_table_matches_the_columns_the_sync_module_writes(self):
        """建表语句和 `screener.sync.balancesheet.COLUMNS` 都是手写的，顺序错了会错列落库。"""
        from screener.sync.balancesheet import COLUMNS as BALANCESHEET_COLUMNS

        self.assertEqual(
            [name for name, _ in self._columns("balancesheet")], list(BALANCESHEET_COLUMNS)
        )

    def test_balancesheet_primary_key_is_one_row_per_quarter(self):
        """上游对同期会回多版本，落库前已经压成一行，所以主键里不能再有公告日。"""
        primary = sorted((pk, name) for name, pk in self._columns("balancesheet") if pk)

        self.assertEqual(
            [name for _, name in primary],
            ["ts_code", "end_date", "report_type"],
        )

    def test_balancesheet_has_an_end_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'balancesheet'"
            )
        ]

        self.assertIn("idx_balancesheet_end_date", names)

    def test_balancesheet_rejects_a_second_row_for_the_same_quarter(self):
        columns = ("ts_code", "end_date", "report_type")
        row = ("000001.SZ", "20260930", "1")

        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(
                self.connection,
                table="balancesheet",
                columns=columns,
                rows=[row, row],
            )

    def test_fina_indicator_table_matches_the_columns_the_sync_module_writes(self):
        """建表语句和 `screener.sync.fina_indicator.COLUMNS` 都是手写的，顺序错了会错列落库。"""
        from screener.sync.fina_indicator import COLUMNS as FINA_INDICATOR_COLUMNS

        self.assertEqual(
            [name for name, _ in self._columns("fina_indicator")],
            list(FINA_INDICATOR_COLUMNS),
        )

    def test_fina_indicator_primary_key_is_one_row_per_quarter(self):
        """该接口没有 report_type，同一报告期的修订版本已在上游侧压成一行。"""
        primary = sorted((pk, name) for name, pk in self._columns("fina_indicator") if pk)

        self.assertEqual([name for _, name in primary], ["ts_code", "end_date"])

    def test_fina_indicator_has_an_end_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'index' AND tbl_name = 'fina_indicator'"
            )
        ]

        self.assertIn("idx_fina_indicator_end_date", names)

    def test_fina_indicator_rejects_a_second_row_for_the_same_quarter(self):
        columns = ("ts_code", "end_date")
        row = ("000001.SZ", "20260930")

        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(
                self.connection,
                table="fina_indicator",
                columns=columns,
                rows=[row, row],
            )

    def test_dividend_table_matches_the_columns_the_sync_module_writes(self):
        """建表语句和 `screener.sync.dividend.COLUMNS` 都是手写的，顺序错了会错列落库。"""
        from screener.sync.dividend import COLUMNS as DIVIDEND_COLUMNS

        self.assertEqual(
            [name for name, _ in self._columns("dividend")],
            list(DIVIDEND_COLUMNS),
        )

    def test_dividend_primary_key_is_one_row_per_scheme(self):
        """只存已实施的方案，同一报告期已在上游侧压成一行，主键里不再有公告日。"""
        primary = sorted((pk, name) for name, pk in self._columns("dividend") if pk)

        self.assertEqual([name for _, name in primary], ["ts_code", "end_date"])

    def test_dividend_has_an_end_date_index_and_a_stock_ex_date_index(self):
        names = [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'dividend'"
            )
        ]

        self.assertIn("idx_dividend_end_date", names)
        self.assertIn("idx_dividend_ex_date", names)

    def test_dividend_rejects_a_second_row_for_the_same_scheme(self):
        columns = ("ts_code", "end_date")
        row = ("600519.SH", "20241231")

        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(
                self.connection,
                table="dividend",
                columns=columns,
                rows=[row, row],
            )
