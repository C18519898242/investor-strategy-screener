import logging
import shutil
import sqlite3
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


class InterruptingClient(FakeClient):
    """第 N 次调用直接抛 KeyboardInterrupt，模拟 Ctrl+C 打断。"""

    def __init__(self, rows=(), *, interrupt_on):
        super().__init__(rows)
        self.interrupt_on = interrupt_on

    def suspend_d(self, **params):
        if len(self.calls) + 1 == self.interrupt_on:
            raise KeyboardInterrupt
        return super().suspend_d(**params)


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

    def test_every_chunk_is_announced_before_its_request(self):
        timeline = []

        class Recorder(logging.Handler):
            def emit(self, record):
                timeline.append(("log", record.getMessage()))

        client = FakeClient()
        request = client.suspend_d

        def recording_request(**params):
            timeline.append(("fetch", params["start_date"]))
            return request(**params)

        client.suspend_d = recording_request
        logger = logging.getLogger("screener.sync.suspend_d")
        recorder = Recorder()
        with self.assertLogs("screener.sync.suspend_d", level="INFO") as captured:
            logger.addHandler(recorder)
            try:
                fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))
            finally:
                logger.removeHandler(recorder)

        self.assertEqual(len(captured.output), 149)
        self.assertEqual(timeline[0], ("log", "停复牌 第 1/149 段：20151018 ~ 20151113"))
        self.assertEqual(timeline[1], ("fetch", "20151018"))
        self.assertEqual(timeline[-2], ("log", "停复牌 第 149/149 段：20260926 ~ 20261018"))
        self.assertEqual(timeline[-1], ("fetch", "20260926"))
        self.assertEqual(len(timeline), 298)

    def test_chunks_written_before_an_interruption_stay_on_disk(self):
        client = InterruptingClient([row("20151020")], interrupt_on=3)

        with self.assertRaises(KeyboardInterrupt):
            fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        reopened = sqlite3.connect(self.directory / "screener.sqlite3", timeout=0.2)
        try:
            self.assertEqual(
                reopened.execute("SELECT trade_date FROM suspend_d").fetchall(),
                [("20151020",)],
            )
        finally:
            reopened.close()

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

    def test_water_level_after_today_deletes_nothing_and_writes_nothing(self):
        client = FakeClient([row("20261020")])
        fetch_suspend_d(self.connection, client, today=date(2026, 10, 25))
        client.calls.clear()

        count = fetch_suspend_d(self.connection, client, today=date(2026, 10, 18))

        self.assertEqual(count, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(
            self.connection.execute("SELECT trade_date FROM suspend_d").fetchall(),
            [("20261020",)],
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
