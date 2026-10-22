import shutil
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.sync import dividend as dividend_module
from screener.sync.dividend import (
    COLUMNS,
    KEY,
    collapse_cross_period,
    fetch_dividend,
    keep_latest,
    record_dates_after_water,
    water_level,
)
from tests.tushare_fakes import dividend_row

TODAY = date(2026, 10, 2)
SEED = "20260930"


def scheme(ts_code="600519.SH", period="20260930", **overrides):
    """一行已实施的分红方案，只填几个关键字段，其余靠 dividend_row 兜成 None。"""
    defaults = {
        "ts_code": ts_code,
        "end_date": period,
        "ann_date": "20260920",
        "div_proc": "实施",
        "imp_ann_date": "20260920",
        "cash_div_tax": 1.0,
        "base_share": 1000.0,
        "record_date": "20261002",
        "ex_date": "20261009",
    }
    return dividend_row(**{**defaults, **overrides})


def values(**overrides):
    row = scheme(**overrides)
    return tuple(row[name] for name in COLUMNS)


def collapse(*specs):
    rows = [tuple({**dict.fromkeys(COLUMNS), **spec}[name] for name in COLUMNS) for spec in specs]
    return [dict(zip(COLUMNS, row, strict=False)) for row in keep_latest(rows)]


ONE_QUARTER = {"ts_code": "600519.SH", "end_date": "20241231", "div_proc": "实施"}


class KeepLatestTest(unittest.TestCase):
    """同一报告期的多条实施行压成一行（2026-10-21 的决定）。"""

    def test_the_later_implementation_announcement_wins(self):
        """实测 600519 2024 年报：两条实施行金额相同，公告日 20250403 与 20250520。"""
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20250403", "imp_ann_date": "20250620"},
            {**ONE_QUARTER, "ann_date": "20250520", "imp_ann_date": "20250620"},
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["ann_date"], "20250520")

    def test_a_later_implementation_date_beats_a_later_announcement_date(self):
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20250520", "imp_ann_date": "20250620"},
            {**ONE_QUARTER, "ann_date": "20250403", "imp_ann_date": "20250701"},
        )

        self.assertEqual(kept[0]["imp_ann_date"], "20250701")

    def test_different_periods_are_all_kept(self):
        kept = collapse(
            {**ONE_QUARTER, "end_date": "20240630"},
            {**ONE_QUARTER, "end_date": "20241231"},
            {**ONE_QUARTER, "end_date": "20250630"},
        )

        self.assertEqual(
            sorted(row["end_date"] for row in kept),
            ["20240630", "20241231", "20250630"],
        )

    def test_stocks_are_collapsed_independently(self):
        kept = collapse(
            {**ONE_QUARTER, "ts_code": "600519.SH"}, {**ONE_QUARTER, "ts_code": "000001.SZ"}
        )

        self.assertEqual(sorted(row["ts_code"] for row in kept), ["000001.SZ", "600519.SH"])

    def test_an_empty_list_stays_empty(self):
        self.assertEqual(keep_latest([]), [])


class DividendTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")
        insert_rows(
            self.connection,
            table="trade_cal",
            columns=("exchange", "cal_date", "is_open"),
            rows=[
                ("SSE", "20260929", 1),
                ("SSE", "20260930", 1),
                ("SSE", "20261001", 0),
                ("SSE", "20261002", 1),
            ],
        )
        self.original_fetch_page = dividend_module._fetch_page
        self.requests = []

    def tearDown(self):
        dividend_module._fetch_page = self.original_fetch_page
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def seed(self, record_date=SEED, **overrides):
        insert_rows(
            self.connection,
            table="dividend",
            columns=COLUMNS,
            rows=[values(record_date=record_date, **overrides)],
        )

    def serve(self, *pages):
        """按顺序给出每一页：(行列表, has_more)。"""
        queue = list(pages)

        def fake(record_date, offset):
            self.requests.append((record_date, offset))
            return queue.pop(0) if queue else ([], False)

        dividend_module._fetch_page = fake

    def _stored(self):
        return [
            (row[0], row[1])
            for row in self.connection.execute(
                "SELECT ts_code, end_date FROM dividend ORDER BY ts_code, end_date"
            )
        ]

    def test_only_implemented_schemes_are_stored(self):
        self.seed()
        self.serve(
            (
                [
                    scheme(),
                    scheme(div_proc="预案", cash_div_tax=99.0),
                    scheme(div_proc="股东大会通过", cash_div_tax=98.0),
                ],
                False,
            )
        )

        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(count, 1)
        self.assertEqual(self._stored(), [("600519.SH", "20260930")])

    def test_the_request_is_one_record_date_at_a_time(self):
        self.seed()
        self.serve(([scheme()], False))

        fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(self.requests, [("20261002", 0)])

    def test_only_trading_days_after_the_water_mark_are_requested(self):
        self.seed()
        self.serve(([], False))

        fetch_dividend(self.connection, None, today=date(2026, 10, 5))

        self.assertEqual([record_date for record_date, _ in self.requests], ["20261002"])

    def test_an_empty_table_requests_nothing(self):
        """历史由回填脚本灌，菜单不去猜起点。"""
        self.serve(([scheme()], False))

        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(self.requests, [])
        self.assertEqual(count, 0)

    def test_rows_of_any_period_are_kept(self):
        """增量按登记日走，一条记录属于哪个报告期不影响入库。"""
        self.seed()
        self.serve(
            (
                [
                    scheme(period="20260630", ex_date="20260710"),
                    scheme(period="20260930", ex_date="20261009"),
                ],
                False,
            )
        )

        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(count, 2)
        self.assertEqual(self._stored(), [("600519.SH", "20260630"), ("600519.SH", "20260930")])

    def test_two_implemented_rows_of_one_scheme_are_stored_once(self):
        self.seed()
        self.serve(
            (
                [
                    scheme(ann_date="20260920", imp_ann_date="20260920"),
                    scheme(ann_date="20260925", imp_ann_date="20260925"),
                ],
                False,
            )
        )

        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(count, 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM dividend").fetchone(), (1,))

    def test_a_full_page_triggers_another_request(self):
        self.seed()
        self.serve(
            ([scheme(ts_code="000001.SZ"), scheme(ts_code="000002.SZ")], True),
            ([scheme(ts_code="000004.SZ")], False),
        )

        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(self.requests, [("20261002", 0), ("20261002", 2)])
        self.assertEqual(count, 3)

    def test_the_water_mark_advances_so_a_second_run_asks_for_nothing(self):
        self.seed()
        self.serve(([scheme()], False))
        fetch_dividend(self.connection, None, today=TODAY)

        self.requests.clear()
        self.serve()
        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(self.requests, [])
        self.assertEqual(count, 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM dividend").fetchone(), (1,))

    def test_an_empty_answer_writes_nothing(self):
        self.seed()
        self.serve()

        count = fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM dividend").fetchone(), (1,))

    def test_the_water_mark_is_the_latest_record_date(self):
        self.seed(record_date="20260929")

        self.assertEqual(water_level(self.connection, TODAY), "20260929")
        self.assertEqual(record_dates_after_water(self.connection, TODAY), ("20260930", "20261002"))

    def test_a_future_record_date_does_not_push_the_water_mark_past_today(self):
        """回填会带进已公告但未到登记日的方案，水位不能因此跳过中间的日子。"""
        self.seed(record_date="20260929", ts_code="000001.SZ")
        self.seed(record_date="20261231", ts_code="000002.SZ")
        self.serve(([], False))

        fetch_dividend(self.connection, None, today=TODAY)

        self.assertEqual(water_level(self.connection, TODAY), "20260929")
        self.assertEqual(
            [record_date for record_date, _ in self.requests], ["20260930", "20261002"]
        )

    def test_the_same_payout_under_two_periods_keeps_the_earlier_one(self):
        """实测宁德时代 2024 中期：同一笔 1.23 元挂在 20240930 和 20241211 下。"""
        insert_rows(
            self.connection,
            table="dividend",
            columns=COLUMNS,
            rows=[
                values(
                    ts_code="300750.SZ", period="20241211", cash_div_tax=1.23, ex_date="20250124"
                )
            ],
        )
        insert_rows(
            self.connection,
            table="dividend",
            columns=COLUMNS,
            rows=[
                values(
                    ts_code="300750.SZ", period="20240930", cash_div_tax=1.23, ex_date="20250124"
                )
            ],
        )

        removed = collapse_cross_period(self.connection)

        self.assertEqual(removed, 1)
        self.assertEqual(self._stored(), [("300750.SZ", "20240930")])

    def test_different_amounts_on_the_same_ex_date_are_both_kept(self):
        insert_rows(
            self.connection,
            table="dividend",
            columns=COLUMNS,
            rows=[
                values(period="20241211", cash_div_tax=1.23, ex_date="20250124"),
                values(period="20240930", cash_div_tax=0.5, ex_date="20250124"),
            ],
        )

        removed = collapse_cross_period(self.connection)

        self.assertEqual(removed, 0)
        self.assertEqual(len(self._stored()), 2)

    def test_rows_without_an_ex_date_are_never_collapsed(self):
        insert_rows(
            self.connection,
            table="dividend",
            columns=COLUMNS,
            rows=[
                values(period="20241211", ex_date=None),
                values(period="20240930", ex_date=None),
            ],
        )

        removed = collapse_cross_period(self.connection)

        self.assertEqual(removed, 0)
        self.assertEqual(len(self._stored()), 2)

    def test_the_primary_key_is_the_stock_and_the_period(self):
        columns = ("ts_code", "end_date")
        row = ("600519.SH", "20260930")

        with self.assertRaises(sqlite3.IntegrityError):
            insert_rows(self.connection, table="dividend", columns=columns, rows=[row, row])

        self.assertEqual(KEY, columns)


if __name__ == "__main__":
    unittest.main()
