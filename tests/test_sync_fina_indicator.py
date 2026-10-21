import logging
import shutil
import unittest
from datetime import date
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.sync.fina_indicator import COLUMNS, fetch_fina_indicator, keep_latest
from tests.tushare_fakes import FakeTushareClient, fina_indicator_row

TODAY = date(2026, 11, 1)


def statement(ts_code="000001.SZ", period="20260930", **overrides):
    """一行财务指标，只填几个关键字段，其余靠 fina_indicator_row 兜成 None。"""
    defaults = {
        "ts_code": ts_code,
        "end_date": period,
        "ann_date": "20261020",
        "update_flag": "1",
        "roe_waa": 12.5,
        "debt_to_assets": 40.0,
    }
    return fina_indicator_row(**{**defaults, **overrides})


def values(ts_code="000001.SZ", period="20260930", **overrides):
    """insert_rows 收的是序列，所以按列顺序把上面那行转成元组。"""
    row = statement(ts_code, period, **overrides)
    return tuple(row[name] for name in COLUMNS)


def collapse(*specs):
    """跑一遍 keep_latest，再把结果按列名还原成字典，方便断言。"""
    rows = [tuple({**dict.fromkeys(COLUMNS), **spec}[name] for name in COLUMNS) for spec in specs]
    return [dict(zip(COLUMNS, row, strict=False)) for row in keep_latest(rows)]


ONE_QUARTER = {"ts_code": "603366.SH", "end_date": "20250630"}


class KeepLatestTest(unittest.TestCase):
    """上游对同一个报告期会回修订版本，落库前必须压成一行（2026-10-21 的决定）。"""

    def test_the_later_announcement_wins(self):
        """实测 301173.SZ 2024 年报：早的一行 roe_waa 是空，修订后才有值。"""
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20250212", "roe_waa": None},
            {**ONE_QUARTER, "ann_date": "20250425", "roe_waa": 23.43},
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["ann_date"], "20250425")
        self.assertEqual(kept[0]["roe_waa"], 23.43)

    def test_the_update_flag_breaks_a_same_day_tie(self):
        """实测多版本全部是同公告日、update_flag 0/1 成对，1 是新版本。"""
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20250815", "update_flag": "0", "roe_waa": 1.0},
            {**ONE_QUARTER, "ann_date": "20250815", "update_flag": "1", "roe_waa": 2.0},
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["roe_waa"], 2.0)

    def test_a_later_announcement_beats_a_higher_update_flag(self):
        """公告日优先：老版本公告日更晚时，不能只看 update_flag。"""
        kept = collapse(
            {**ONE_QUARTER, "ann_date": "20250401", "update_flag": "1", "roe_waa": 1.0},
            {**ONE_QUARTER, "ann_date": "20250815", "update_flag": "0", "roe_waa": 2.0},
        )

        self.assertEqual(kept[0]["roe_waa"], 2.0)

    def test_different_quarters_are_all_kept(self):
        kept = collapse(
            {**ONE_QUARTER, "end_date": "20250331"},
            {**ONE_QUARTER, "end_date": "20250630"},
            {**ONE_QUARTER, "end_date": "20250930"},
        )

        self.assertEqual(
            sorted(row["end_date"] for row in kept),
            ["20250331", "20250630", "20250930"],
        )

    def test_stocks_are_collapsed_independently(self):
        kept = collapse(
            {**ONE_QUARTER, "ts_code": "603366.SH"},
            {**ONE_QUARTER, "ts_code": "000001.SZ"},
        )

        self.assertEqual(sorted(row["ts_code"] for row in kept), ["000001.SZ", "603366.SH"])

    def test_an_empty_list_stays_empty(self):
        self.assertEqual(keep_latest([]), [])


class FakePeriodIgnorer(FakeTushareClient):
    """不管请求哪一期，都回同一期的数据，用来验证落库前会按报告期过滤。"""

    def fina_indicator(self, **params):
        self.calls.append(("fina_indicator", params))
        return self._slice([statement("000001.SZ", "20260630")], params["fields"].split(","))


class FinaIndicatorTest(unittest.TestCase):
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
            (row[0], row[1])
            for row in self.connection.execute(
                "SELECT ts_code, end_date FROM fina_indicator ORDER BY ts_code"
            )
        ]

    def test_every_missing_stock_is_requested_and_stored(self):
        client = FakeTushareClient(
            fina_indicator_rows=[statement("000001.SZ"), statement("000002.SZ")],
        )

        count = fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000001.SZ", "000002.SZ"])
        self.assertEqual(count, 2)
        self.assertEqual(
            self._stored(),
            [("000001.SZ", "20260930"), ("000002.SZ", "20260930")],
        )

    def test_the_request_names_one_period_and_all_columns(self):
        client = FakeTushareClient(fina_indicator_rows=[statement("000001.SZ")])

        fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertEqual(
            client.calls[0],
            (
                "fina_indicator",
                {
                    "ts_code": "000001.SZ",
                    "period": "20260930",
                    "fields": ",".join(COLUMNS),
                },
            ),
        )

    def test_the_update_flag_is_always_requested(self):
        """逐股路径默认只返回 108 列、缺 update_flag，所以字段表必须显式点名它。"""
        client = FakeTushareClient(fina_indicator_rows=[statement("000001.SZ")])

        fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertIn("update_flag", client.calls[0][1]["fields"].split(","))
        self.assertEqual(len(client.calls[0][1]["fields"].split(",")), 109)

    def test_two_versions_of_one_quarter_are_stored_as_one_row(self):
        client = FakeTushareClient(
            fina_indicator_rows=[
                statement("000001.SZ", ann_date="20261020", roe_waa=1.0),
                statement("000001.SZ", ann_date="20261025", roe_waa=2.0),
            ],
        )

        count = fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertEqual(count, 1)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM fina_indicator").fetchone(), (1,)
        )
        self.assertEqual(
            self.connection.execute("SELECT roe_waa FROM fina_indicator").fetchone(),
            (2.0,),
        )

    def test_stocks_that_already_have_the_period_are_not_requested(self):
        insert_rows(
            self.connection, table="fina_indicator", columns=COLUMNS, rows=[values("000001.SZ")]
        )
        client = FakeTushareClient(fina_indicator_rows=[statement("000002.SZ")])

        fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000002.SZ"])

    def test_a_second_run_makes_no_request(self):
        fetch_fina_indicator(
            self.connection,
            FakeTushareClient(fina_indicator_rows=[statement("000001.SZ"), statement("000002.SZ")]),
            today=TODAY,
        )
        client = FakeTushareClient()

        count = fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertEqual(client.calls, [])
        self.assertEqual(count, 0)

    def test_the_april_deadline_backfills_the_annual_report_too(self):
        client = FakeTushareClient(
            fina_indicator_rows=[
                statement("000001.SZ", "20260331"),
                statement("000002.SZ", "20260331"),
                statement("000001.SZ", "20251231"),
                statement("000002.SZ", "20251231"),
            ],
        )

        count = fetch_fina_indicator(self.connection, client, today=date(2026, 4, 30))

        self.assertEqual(
            [params["period"] for _, params in client.calls],
            ["20260331", "20260331", "20251231", "20251231"],
        )
        self.assertEqual(count, 4)

    def test_an_empty_answer_writes_nothing(self):
        count = fetch_fina_indicator(self.connection, FakeTushareClient(), today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM fina_indicator").fetchone(), (0,)
        )

    def test_rows_of_another_period_are_dropped(self):
        count = fetch_fina_indicator(self.connection, FakePeriodIgnorer(), today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM fina_indicator").fetchone(), (0,)
        )

    def test_null_values_are_stored_as_sql_null(self):
        client = FakeTushareClient(
            fina_indicator_rows=[statement("000001.SZ", roe_waa=None, roe=None)]
        )

        fetch_fina_indicator(self.connection, client, today=TODAY)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM fina_indicator WHERE roe_waa IS NULL AND roe IS NULL"
            ).fetchone(),
            (1,),
        )

    def test_every_stock_is_announced_before_its_request(self):
        timeline = []

        class Recorder(logging.Handler):
            def emit(self, record):
                timeline.append(("log", record.getMessage()))

        client = FakeTushareClient(
            fina_indicator_rows=[statement("000001.SZ"), statement("000002.SZ")]
        )
        request = client.fina_indicator

        def recording_request(**params):
            timeline.append(("fetch", params["ts_code"]))
            return request(**params)

        client.fina_indicator = recording_request
        logger = logging.getLogger("screener.sync.fina_indicator")
        recorder = Recorder()
        with self.assertLogs("screener.sync.fina_indicator", level="INFO") as captured:
            logger.addHandler(recorder)
            try:
                fetch_fina_indicator(self.connection, client, today=TODAY)
            finally:
                logger.removeHandler(recorder)

        self.assertIn(
            "INFO:screener.sync.fina_indicator:财务指标 20260930：本地缺 2 只", captured.output
        )
        self.assertEqual(timeline[0], ("log", "财务指标 20260930：本地缺 2 只"))
        self.assertEqual(timeline[1], ("log", "财务指标 20260930 第 1/2 只：000001.SZ"))
        self.assertEqual(timeline[2], ("fetch", "000001.SZ"))
        self.assertEqual(timeline[-2], ("log", "财务指标 20260930 第 2/2 只：000002.SZ"))
        self.assertEqual(timeline[-1], ("fetch", "000002.SZ"))
        self.assertEqual(len(timeline), 5)


if __name__ == "__main__":
    unittest.main()
