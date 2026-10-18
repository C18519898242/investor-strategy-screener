import shutil
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from screener.store import open_database
from screener.sync.trade_cal import COLUMNS, fetch_trade_cal


class FakeClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def trade_cal(self, **params):
        self.calls.append(params)
        return self.frame


def row(cal_date, **overrides):
    return {"exchange": "SSE", "cal_date": cal_date, "is_open": 1, "pretrade_date": "", **overrides}


def frame_of(rows):
    return pd.DataFrame(rows, columns=list(COLUMNS))


class TradeCalTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_requests_sse_eleven_years_back_in_one_call(self):
        client = FakeClient(frame_of([]))

        fetch_trade_cal(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            client.calls[0],
            {
                "exchange": "SSE",
                "start_date": "20151018",
                "end_date": "20261018",
                "fields": "exchange,cal_date,is_open,pretrade_date",
            },
        )

    def test_writes_the_rows_and_returns_the_count(self):
        rows = [row("20150105"), row("20150106", pretrade_date="20150105")]

        count = fetch_trade_cal(
            self.connection, FakeClient(frame_of(rows)), today=date(2026, 10, 18)
        )

        self.assertEqual(count, 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT cal_date, is_open, pretrade_date FROM trade_cal ORDER BY cal_date"
            ).fetchall(),
            [("20150105", 1, ""), ("20150106", 1, "20150105")],
        )

    def test_a_second_run_replaces_the_previous_rows(self):
        fetch_trade_cal(
            self.connection, FakeClient(frame_of([row("20150105")])), today=date(2026, 10, 18)
        )
        fetch_trade_cal(
            self.connection,
            FakeClient(frame_of([row("20150105"), row("20150106", is_open=0)])),
            today=date(2026, 10, 18),
        )

        self.assertEqual(
            self.connection.execute(
                "SELECT cal_date, is_open FROM trade_cal ORDER BY cal_date"
            ).fetchall(),
            [("20150105", 1), ("20150106", 0)],
        )
