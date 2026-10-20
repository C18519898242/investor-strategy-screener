import logging
import shutil
import unittest
from datetime import date
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.sync.income import COLUMNS, REPORT_TYPE, fetch_income, target_periods
from tests.tushare_fakes import FakeTushareClient, income_row

TODAY = date(2026, 11, 1)


def statement(ts_code="000001.SZ", period="20260930", **overrides):
    """一行单季利润表，只填几个关键字段，其余靠 income_row 兜成 None。"""
    defaults = {
        "ts_code": ts_code,
        "end_date": period,
        "report_type": REPORT_TYPE,
        "ann_date": "20261020",
        "update_flag": "0",
        "n_income_attr_p": 1000.0,
        "revenue": 5000.0,
    }
    return income_row(**{**defaults, **overrides})


def values(ts_code="000001.SZ", period="20260930", **overrides):
    """insert_rows 收的是序列，所以按列顺序把上面那行转成元组。"""
    row = statement(ts_code, period, **overrides)
    return tuple(row[name] for name in COLUMNS)


class FakePeriodIgnorer(FakeTushareClient):
    """不管请求哪一期，都回同一期的数据，用来验证落库前会按报告期过滤。"""

    def income(self, **params):
        self.calls.append(("income", params))
        return self._slice([statement("000001.SZ", "20260630")], params["fields"].split(","))


class IncomeTest(unittest.TestCase):
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
                "SELECT ts_code, end_date, report_type FROM income ORDER BY ts_code"
            )
        ]

    def test_the_target_period_follows_the_disclosure_deadlines(self):
        self.assertEqual(target_periods(date(2026, 11, 1)), ("20260930",))
        self.assertEqual(target_periods(date(2026, 10, 31)), ("20260930",))
        # 三季报的截止日是 10 月 31 日，在那之前最近的已到期期次还是中报。
        self.assertEqual(target_periods(date(2026, 10, 20)), ("20260630",))
        self.assertEqual(target_periods(date(2026, 8, 31)), ("20260630",))
        self.assertEqual(target_periods(date(2026, 8, 30)), ("20260331", "20251231"))
        self.assertEqual(target_periods(date(2026, 5, 1)), ("20260331", "20251231"))
        self.assertEqual(target_periods(date(2026, 4, 30)), ("20260331", "20251231"))
        self.assertEqual(target_periods(date(2026, 4, 29)), ("20250930",))
        self.assertEqual(target_periods(date(2026, 1, 15)), ("20250930",))

    def test_every_missing_stock_is_requested_and_stored(self):
        client = FakeTushareClient(
            income_rows=[statement("000001.SZ"), statement("000002.SZ")],
        )

        count = fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000001.SZ", "000002.SZ"])
        self.assertEqual(count, 2)
        self.assertEqual(
            self._stored(),
            [("000001.SZ", "20260930", "2"), ("000002.SZ", "20260930", "2")],
        )

    def test_the_request_names_one_period_and_the_single_quarter_caliber(self):
        client = FakeTushareClient(income_rows=[statement("000001.SZ")])

        fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(
            client.calls[0],
            (
                "income",
                {
                    "ts_code": "000001.SZ",
                    "period": "20260930",
                    "report_type": "2",
                    "fields": ",".join(COLUMNS),
                },
            ),
        )

    def test_stocks_that_already_have_the_period_are_not_requested(self):
        insert_rows(self.connection, table="income", columns=COLUMNS, rows=[values("000001.SZ")])
        client = FakeTushareClient(income_rows=[statement("000002.SZ")])

        fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000002.SZ"])

    def test_a_cumulative_row_does_not_count_as_the_single_quarter_row(self):
        """判定粒度是「股票 + 报告期 + 口径」，只看报告期会把另一口径误当成已有。"""
        insert_rows(
            self.connection,
            table="income",
            columns=COLUMNS,
            rows=[values("000001.SZ", report_type="1")],
        )
        client = FakeTushareClient(income_rows=[statement("000001.SZ")])

        fetch_income(self.connection, client, today=TODAY)

        self.assertIn("000001.SZ", self._requested(client))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM income WHERE report_type = ?", (REPORT_TYPE,)
            ).fetchone(),
            (1,),
        )

    def test_a_second_run_makes_no_request(self):
        fetch_income(
            self.connection,
            FakeTushareClient(income_rows=[statement("000001.SZ"), statement("000002.SZ")]),
            today=TODAY,
        )
        client = FakeTushareClient()

        count = fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(client.calls, [])
        self.assertEqual(count, 0)

    def test_the_april_deadline_backfills_the_annual_report_too(self):
        client = FakeTushareClient(
            income_rows=[
                statement("000001.SZ", "20260331"),
                statement("000002.SZ", "20260331"),
                statement("000001.SZ", "20251231"),
                statement("000002.SZ", "20251231"),
            ],
        )

        count = fetch_income(self.connection, client, today=date(2026, 4, 30))

        self.assertEqual(
            [params["period"] for _, params in client.calls],
            ["20260331", "20260331", "20251231", "20251231"],
        )
        self.assertEqual(count, 4)

    def test_before_the_april_deadline_only_last_years_third_quarter_is_fetched(self):
        client = FakeTushareClient(income_rows=[statement("000001.SZ", "20250930")])

        fetch_income(self.connection, client, today=date(2026, 3, 1))

        self.assertEqual({params["period"] for _, params in client.calls}, {"20250930"})

    def test_an_empty_answer_writes_nothing(self):
        client = FakeTushareClient()

        count = fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM income").fetchone(), (0,))

    def test_rows_of_another_period_are_dropped(self):
        client = FakePeriodIgnorer()

        count = fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM income").fetchone(), (0,))

    def test_null_values_are_stored_as_sql_null(self):
        statement_with_nulls = statement("000001.SZ", n_income_attr_p=None, revenue=None)
        client = FakeTushareClient(income_rows=[statement_with_nulls])

        fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM income WHERE n_income_attr_p IS NULL AND revenue IS NULL"
            ).fetchone(),
            (1,),
        )

    def test_only_listed_stocks_are_requested(self):
        self.connection.execute("DELETE FROM stock_basic WHERE ts_code = '000002.SZ'")
        client = FakeTushareClient(income_rows=[statement("000002.SZ")])

        fetch_income(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000001.SZ"])

    def test_every_stock_is_announced_before_its_request(self):
        timeline = []

        class Recorder(logging.Handler):
            def emit(self, record):
                timeline.append(("log", record.getMessage()))

        client = FakeTushareClient(
            income_rows=[statement("000001.SZ"), statement("000002.SZ")],
        )
        request = client.income

        def recording_request(**params):
            timeline.append(("fetch", params["ts_code"]))
            return request(**params)

        client.income = recording_request
        logger = logging.getLogger("screener.sync.income")
        recorder = Recorder()
        with self.assertLogs("screener.sync.income", level="INFO") as captured:
            logger.addHandler(recorder)
            try:
                fetch_income(self.connection, client, today=TODAY)
            finally:
                logger.removeHandler(recorder)

        self.assertIn("INFO:screener.sync.income:利润表 20260930：本地缺 2 只", captured.output)
        self.assertEqual(timeline[0], ("log", "利润表 20260930：本地缺 2 只"))
        self.assertEqual(timeline[1], ("log", "利润表 20260930 第 1/2 只：000001.SZ"))
        self.assertEqual(timeline[2], ("fetch", "000001.SZ"))
        self.assertEqual(timeline[-2], ("log", "利润表 20260930 第 2/2 只：000002.SZ"))
        self.assertEqual(timeline[-1], ("fetch", "000002.SZ"))
        self.assertEqual(len(timeline), 5)


if __name__ == "__main__":
    unittest.main()
