import logging
import shutil
import unittest
from datetime import date
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.sync.balancesheet import COLUMNS, REPORT_TYPE, fetch_balancesheet, keep_latest
from tests.tushare_fakes import FakeTushareClient, balancesheet_row

TODAY = date(2026, 11, 1)


def statement(ts_code="000001.SZ", period="20260930", **overrides):
    """一行资产负债表，只填几个关键字段，其余靠 balancesheet_row 兜成 None。"""
    defaults = {
        "ts_code": ts_code,
        "end_date": period,
        "report_type": REPORT_TYPE,
        "ann_date": "20261020",
        "f_ann_date": "20261020",
        "update_flag": "0",
        "total_assets": 1000.0,
        "total_liab": 400.0,
    }
    return balancesheet_row(**{**defaults, **overrides})


def values(ts_code="000001.SZ", period="20260930", **overrides):
    """insert_rows 收的是序列，所以按列顺序把上面那行转成元组。"""
    row = statement(ts_code, period, **overrides)
    return tuple(row[name] for name in COLUMNS)


def collapse(*specs):
    """跑一遍 keep_latest，再把结果按列名还原成字典，方便断言。"""
    rows = [tuple({**dict.fromkeys(COLUMNS), **spec}[name] for name in COLUMNS) for spec in specs]
    return [dict(zip(COLUMNS, row, strict=False)) for row in keep_latest(rows)]


ONE_QUARTER = {"ts_code": "600076.SH", "end_date": "20221231", "report_type": "1"}


class KeepLatestTest(unittest.TestCase):
    """上游对同一个季度会回多行，落库前必须压成一行（2026-10-20 的决定）。"""

    def test_the_restated_version_wins_when_the_announcement_date_moves(self):
        """600076.SH 2022 年报在 2026-04-30 被重述过，ann_date 和 update_flag 都没变。"""
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20230422", "f_ann_date": "20230422", "total_assets": 1.0},
            {**ONE_QUARTER, "ann_date": "20230422", "f_ann_date": "20260430", "total_assets": 2.0},
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["total_assets"], 2.0)
        self.assertEqual(kept[0]["f_ann_date"], "20260430")

    def test_the_update_flag_pair_collapses_to_one_row(self):
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20260815", "f_ann_date": "20260815", "update_flag": "0"},
            {**ONE_QUARTER, "ann_date": "20260815", "f_ann_date": "20260815", "update_flag": "1"},
        )

        self.assertEqual(len(kept), 1)

    def test_a_missing_actual_announcement_date_loses_to_a_dated_one(self):
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20230422", "f_ann_date": None, "total_assets": 1.0},
            {**ONE_QUARTER, "ann_date": "20230422", "f_ann_date": "20230422", "total_assets": 2.0},
        )

        self.assertEqual(kept[0]["total_assets"], 2.0)

    def test_different_quarters_are_all_kept(self):
        kept = collapse(
            {**ONE_QUARTER, "end_date": "20221231"},
            {**ONE_QUARTER, "end_date": "20230331"},
            {**ONE_QUARTER, "end_date": "20230630"},
        )

        self.assertEqual(
            sorted(row["end_date"] for row in kept),
            ["20221231", "20230331", "20230630"],
        )

    def test_stocks_are_collapsed_independently(self):
        kept = collapse(
            {**ONE_QUARTER, "ts_code": "600076.SH"},
            {**ONE_QUARTER, "ts_code": "000001.SZ"},
        )

        self.assertEqual(sorted(row["ts_code"] for row in kept), ["000001.SZ", "600076.SH"])

    def test_an_empty_list_stays_empty(self):
        self.assertEqual(keep_latest([]), [])


class FakePeriodIgnorer(FakeTushareClient):
    """不管请求哪一期，都回同一期的数据，用来验证落库前会按报告期过滤。"""

    def balancesheet(self, **params):
        self.calls.append(("balancesheet", params))
        return self._slice([statement("000001.SZ", "20260630")], params["fields"].split(","))


class BalancesheetTest(unittest.TestCase):
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

    def _requested(self, client):
        return [params["ts_code"] for _, params in client.calls]

    def _stored(self):
        return [
            (row[0], row[1], row[2])
            for row in self.connection.execute(
                "SELECT ts_code, end_date, report_type FROM balancesheet ORDER BY ts_code"
            )
        ]

    def test_every_missing_stock_is_requested_and_stored(self):
        client = FakeTushareClient(
            balancesheet_rows=[statement("000001.SZ"), statement("000002.SZ")],
        )

        count = fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000001.SZ", "000002.SZ"])
        self.assertEqual(count, 2)
        self.assertEqual(
            self._stored(),
            [("000001.SZ", "20260930", "1"), ("000002.SZ", "20260930", "1")],
        )

    def test_the_request_names_one_period_and_the_merged_caliber(self):
        client = FakeTushareClient(balancesheet_rows=[statement("000001.SZ")])

        fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(
            client.calls[0],
            (
                "balancesheet",
                {
                    "ts_code": "000001.SZ",
                    "period": "20260930",
                    "report_type": "1",
                    "fields": ",".join(COLUMNS),
                },
            ),
        )

    def test_three_versions_of_one_quarter_are_stored_as_one_row(self):
        """接口回三行（一期多版本），入库后必须只有一行。"""
        client = FakeTushareClient(
            balancesheet_rows=[
                statement("000001.SZ", f_ann_date="20261020", total_assets=1.0),
                statement("000001.SZ", f_ann_date="20261025", total_assets=2.0),
                statement("000001.SZ", f_ann_date="20261025", total_assets=3.0),
            ],
        )

        count = fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(count, 1)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM balancesheet").fetchone(), (1,)
        )
        self.assertEqual(
            self.connection.execute("SELECT total_assets FROM balancesheet").fetchone(),
            (3.0,),
        )

    def test_stocks_that_already_have_the_period_are_not_requested(self):
        insert_rows(
            self.connection, table="balancesheet", columns=COLUMNS, rows=[values("000001.SZ")]
        )
        client = FakeTushareClient(balancesheet_rows=[statement("000002.SZ")])

        fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000002.SZ"])

    def test_a_second_run_makes_no_request(self):
        fetch_balancesheet(
            self.connection,
            FakeTushareClient(balancesheet_rows=[statement("000001.SZ"), statement("000002.SZ")]),
            today=TODAY,
        )
        client = FakeTushareClient()

        count = fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(client.calls, [])
        self.assertEqual(count, 0)

    def test_the_april_deadline_backfills_the_annual_report_too(self):
        client = FakeTushareClient(
            balancesheet_rows=[
                statement("000001.SZ", "20260331"),
                statement("000002.SZ", "20260331"),
                statement("000001.SZ", "20251231"),
                statement("000002.SZ", "20251231"),
            ],
        )

        count = fetch_balancesheet(self.connection, client, today=date(2026, 4, 30))

        self.assertEqual(
            [params["period"] for _, params in client.calls],
            ["20260331", "20260331", "20251231", "20251231"],
        )
        self.assertEqual(count, 4)

    def test_an_empty_answer_writes_nothing(self):
        count = fetch_balancesheet(self.connection, FakeTushareClient(), today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM balancesheet").fetchone(), (0,)
        )

    def test_rows_of_another_period_are_dropped(self):
        count = fetch_balancesheet(self.connection, FakePeriodIgnorer(), today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM balancesheet").fetchone(), (0,)
        )

    def test_null_values_are_stored_as_sql_null(self):
        client = FakeTushareClient(
            balancesheet_rows=[statement("000001.SZ", goodwill=None, money_cap=None)]
        )

        fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM balancesheet WHERE goodwill IS NULL AND money_cap IS NULL"
            ).fetchone(),
            (1,),
        )

    def test_only_listed_stocks_are_requested(self):
        self.connection.execute("DELETE FROM stock_basic WHERE ts_code = '000002.SZ'")
        client = FakeTushareClient(balancesheet_rows=[statement("000002.SZ")])

        fetch_balancesheet(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000001.SZ"])

    def test_every_stock_is_announced_before_its_request(self):
        timeline = []

        class Recorder(logging.Handler):
            def emit(self, record):
                timeline.append(("log", record.getMessage()))

        client = FakeTushareClient(
            balancesheet_rows=[statement("000001.SZ"), statement("000002.SZ")]
        )
        request = client.balancesheet

        def recording_request(**params):
            timeline.append(("fetch", params["ts_code"]))
            return request(**params)

        client.balancesheet = recording_request
        logger = logging.getLogger("screener.sync.balancesheet")
        recorder = Recorder()
        with self.assertLogs("screener.sync.balancesheet", level="INFO") as captured:
            logger.addHandler(recorder)
            try:
                fetch_balancesheet(self.connection, client, today=TODAY)
            finally:
                logger.removeHandler(recorder)

        self.assertIn(
            "INFO:screener.sync.balancesheet:资产负债表 20260930：本地缺 2 只", captured.output
        )
        self.assertEqual(timeline[0], ("log", "资产负债表 20260930：本地缺 2 只"))
        self.assertEqual(timeline[1], ("log", "资产负债表 20260930 第 1/2 只：000001.SZ"))
        self.assertEqual(timeline[2], ("fetch", "000001.SZ"))
        self.assertEqual(timeline[-2], ("log", "资产负债表 20260930 第 2/2 只：000002.SZ"))
        self.assertEqual(timeline[-1], ("fetch", "000002.SZ"))
        self.assertEqual(len(timeline), 5)


if __name__ == "__main__":
    unittest.main()
