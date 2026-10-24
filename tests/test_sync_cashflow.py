import shutil
import unittest
from datetime import date
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.sync.cashflow import COLUMNS, REPORT_TYPE, fetch_cashflow
from tests.tushare_fakes import FakeTushareClient, cashflow_row

TODAY = date(2026, 11, 1)


def statement(ts_code="000001.SZ", period="20260930", **overrides):
    """一行单季现金流量表，只填几个关键字段，其余靠 cashflow_row 兜成 None。"""
    defaults = {
        "ts_code": ts_code,
        "end_date": period,
        "report_type": REPORT_TYPE,
        "ann_date": "20261020",
        "update_flag": "0",
        "n_cashflow_act": 1000.0,
        "c_pay_acq_const_fiolta": 200.0,
    }
    return cashflow_row(**{**defaults, **overrides})


def values(ts_code="000001.SZ", period="20260930", **overrides):
    row = statement(ts_code, period, **overrides)
    return tuple(row[name] for name in COLUMNS)


class CashflowTest(unittest.TestCase):
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
            (row[0], row[1], row[2], row[3])
            for row in self.connection.execute(
                "SELECT ts_code, end_date, report_type, n_cashflow_act"
                " FROM cashflow ORDER BY ts_code"
            )
        ]

    def test_every_missing_stock_is_requested_and_stored(self):
        client = FakeTushareClient(
            cashflow_rows=[statement("000001.SZ"), statement("000002.SZ")],
        )

        count = fetch_cashflow(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000001.SZ", "000002.SZ"])
        self.assertEqual(count, 2)
        self.assertEqual(
            self._stored(),
            [
                ("000001.SZ", "20260930", "2", 1000.0),
                ("000002.SZ", "20260930", "2", 1000.0),
            ],
        )

    def test_the_request_names_one_period_and_the_single_quarter_caliber(self):
        client = FakeTushareClient(cashflow_rows=[statement("000001.SZ")])

        fetch_cashflow(self.connection, client, today=TODAY)

        self.assertEqual(
            client.calls[0],
            (
                "cashflow",
                {
                    "ts_code": "000001.SZ",
                    "period": "20260930",
                    "report_type": "2",
                    "fields": ",".join(COLUMNS),
                },
            ),
        )

    def test_stocks_that_already_have_the_period_are_not_requested(self):
        insert_rows(self.connection, table="cashflow", columns=COLUMNS, rows=[values("000001.SZ")])
        client = FakeTushareClient(cashflow_rows=[statement("000002.SZ")])

        fetch_cashflow(self.connection, client, today=TODAY)

        self.assertEqual(self._requested(client), ["000002.SZ"])

    def test_a_cumulative_row_does_not_count_as_the_single_quarter_row(self):
        insert_rows(
            self.connection,
            table="cashflow",
            columns=COLUMNS,
            rows=[values("000001.SZ", report_type="1")],
        )
        client = FakeTushareClient(cashflow_rows=[statement("000001.SZ")])

        fetch_cashflow(self.connection, client, today=TODAY)

        self.assertIn("000001.SZ", self._requested(client))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM cashflow WHERE report_type = ?", (REPORT_TYPE,)
            ).fetchone(),
            (1,),
        )

    def test_the_capex_and_operating_cash_flow_columns_are_kept(self):
        client = FakeTushareClient(cashflow_rows=[statement("000001.SZ")])

        fetch_cashflow(self.connection, client, today=TODAY)

        row = self.connection.execute(
            "SELECT n_cashflow_act, c_pay_acq_const_fiolta FROM cashflow"
        ).fetchone()
        self.assertEqual(row, (1000.0, 200.0))

    def test_a_stock_without_an_answer_is_requested_again_next_run(self):
        client = FakeTushareClient(cashflow_rows=[statement("000001.SZ")])

        first = fetch_cashflow(self.connection, client, today=TODAY)
        second = fetch_cashflow(self.connection, client, today=TODAY)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(self._requested(client), ["000001.SZ", "000002.SZ", "000002.SZ"])

    def test_two_revisions_of_the_same_period_are_both_kept(self):
        client = FakeTushareClient(
            cashflow_rows=[
                statement("000001.SZ", update_flag="0"),
                statement("000001.SZ", update_flag="1", ann_date="20261025"),
            ]
        )

        count = fetch_cashflow(self.connection, client, today=TODAY)

        self.assertEqual(count, 2)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM cashflow").fetchone()[0],
            2,
        )

    def test_duplicate_rows_of_one_revision_are_collapsed_before_writing(self):
        client = FakeTushareClient(cashflow_rows=[statement("000001.SZ"), statement("000001.SZ")])

        count = fetch_cashflow(self.connection, client, today=TODAY)

        self.assertEqual(count, 1)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM cashflow").fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
