import shutil
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from screener.store import open_database
from screener.sync.suspend_d import fetch_suspend_d


class FakeClient:
    """按区间过滤、倒序返回的假客户端。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def suspend_d(self, **params):
        self.calls.append(params)
        start, end = params["start_date"], params["end_date"]
        picked = [row for row in self.rows if start <= row["trade_date"] <= end]
        return pd.DataFrame(list(reversed(picked)), columns=params["fields"].split(","))


def row(trade_date, **overrides):
    return {
        "ts_code": "000001.SZ",
        "trade_date": trade_date,
        "suspend_timing": None,
        "suspend_type": "S",
        **overrides,
    }


class SuspendDTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_empty_table_starts_eleven_years_back_and_chunks_by_27_days(self):
        client = FakeClient()

        fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(client.calls[0]["start_date"], "20151018")
        self.assertEqual(client.calls[0]["end_date"], "20151113")
        self.assertEqual(client.calls[1]["start_date"], "20151114")
        self.assertEqual(client.calls[-1]["end_date"], "20261018")
        self.assertEqual(
            client.calls[0]["fields"], "ts_code,trade_date,suspend_timing,suspend_type"
        )
        self.assertEqual(len(client.calls), 149)
        self.assertNotIn("suspend_type", client.calls[0])

    def test_water_level_day_is_deleted_and_chunking_starts_there(self):
        client = FakeClient([row("20211001"), row("20211005")])
        fetch_suspend_d(self.connection, client, today=date(2021, 11, 5))
        client.calls.clear()

        fetch_suspend_d(self.connection, client, today=date(2021, 11, 5))

        self.assertEqual(client.calls[0]["start_date"], "20211005")
        self.assertEqual(client.calls[0]["end_date"], "20211031")
        self.assertEqual(
            self.connection.execute(
                "SELECT trade_date FROM suspend_d ORDER BY trade_date"
            ).fetchall(),
            [("20211001",), ("20211005",)],
        )

    def test_rows_are_written_in_trade_date_order(self):
        client = FakeClient([row("20261003"), row("20261001"), row("20261002")])

        fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(
            self.connection.execute("SELECT trade_date FROM suspend_d ORDER BY rowid").fetchall(),
            [("20261001",), ("20261002",), ("20261003",)],
        )

    def test_writes_the_rows_and_returns_the_count(self):
        client = FakeClient(
            [
                row("20261001"),
                row("20261002", suspend_type="R"),
                row("20261002", suspend_timing="09:30-10:00"),
            ]
        )

        count = fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(count, 3)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM suspend_d").fetchone(), (3,))

    def test_null_timing_is_stored_as_sql_null(self):
        fetch_suspend_d(self.connection, FakeClient([row("20261001")]), today=date(2026, 10, 18))

        self.assertEqual(
            self.connection.execute("SELECT suspend_timing FROM suspend_d").fetchone(), (None,)
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM suspend_d WHERE suspend_timing IS NULL"
            ).fetchone(),
            (1,),
        )
