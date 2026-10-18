import logging
import shutil
import sqlite3
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from screener.store import insert_rows, open_database
from screener.sync.daily_basic import COLUMNS, fetch_daily_basic

CALENDAR = [
    ("20261012", 1),
    ("20261013", 1),
    ("20261014", 1),
    ("20261015", 1),
    ("20261016", 1),
    ("20261017", 0),
    ("20261018", 0),
]
TODAY = date(2026, 10, 18)


class FakeClient:
    """按 trade_date 过滤的假客户端，只接受 trade_date 与 fields。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def daily_basic(self, **params):
        self.calls.append(params)
        fields = params["fields"].split(",")
        picked = [row for row in self.rows if row["trade_date"] == params["trade_date"]]
        return pd.DataFrame(
            [{name: row[name] for name in fields} for row in picked], columns=fields
        )


class InterruptingClient(FakeClient):
    """第 N 次调用直接抛 KeyboardInterrupt，模拟 Ctrl+C 打断。"""

    def __init__(self, rows=(), *, interrupt_on):
        super().__init__(rows)
        self.interrupt_on = interrupt_on

    def daily_basic(self, **params):
        if len(self.calls) + 1 == self.interrupt_on:
            raise KeyboardInterrupt
        return super().daily_basic(**params)


def row(trade_date, **overrides):
    values = dict.fromkeys(COLUMNS, 0.0)
    values["ts_code"] = "000001.SZ"
    values["trade_date"] = trade_date
    values["close"] = 10.0
    values["pe"] = 8.0
    return {**values, **overrides}


def values(trade_date, **overrides):
    """insert_rows 收的是序列，所以按列顺序把 row() 的字典转成元组。"""
    data = row(trade_date, **overrides)
    return tuple(data[name] for name in COLUMNS)


class DailyBasicTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")
        insert_rows(
            self.connection,
            table="trade_cal",
            columns=("exchange", "cal_date", "is_open", "pretrade_date"),
            rows=[("SSE", cal_date, is_open, "") for cal_date, is_open in CALENDAR],
        )

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _trading_days(self):
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT trade_date FROM daily_basic ORDER BY trade_date"
            )
        ]

    def test_backfills_every_trading_day_the_calendar_knows(self):
        client = FakeClient([row("20261012"), row("20261016")])

        count = fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual(
            [call["trade_date"] for call in client.calls],
            ["20261012", "20261013", "20261014", "20261015", "20261016"],
        )
        self.assertEqual(set(client.calls[0]), {"trade_date", "fields"})
        self.assertEqual(client.calls[0]["fields"], ",".join(COLUMNS))
        self.assertEqual(count, 2)
        self.assertEqual(self._trading_days(), ["20261012", "20261016"])

    def test_water_level_day_is_deleted_and_refetched(self):
        fetch_daily_basic(
            self.connection, FakeClient([row("20261012"), row("20261013")]), today=TODAY
        )

        client = FakeClient([row("20261013")])
        fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual(
            [call["trade_date"] for call in client.calls],
            ["20261013", "20261014", "20261015", "20261016"],
        )
        self.assertEqual(self._trading_days(), ["20261012", "20261013"])

    def test_a_gap_below_the_water_level_is_filled(self):
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=COLUMNS,
            rows=[values("20261012"), values("20261015")],
        )

        client = FakeClient([row("20261013"), row("20261014"), row("20261015")])
        fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual(
            [call["trade_date"] for call in client.calls],
            ["20261013", "20261014", "20261015", "20261016"],
        )

    def test_an_empty_calendar_fetches_nothing_and_does_not_raise(self):
        self.connection.execute("DELETE FROM trade_cal")
        client = FakeClient()

        count = fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual(count, 0)
        self.assertEqual(client.calls, [])

    def test_the_calendar_end_caps_the_backfill(self):
        self.connection.execute("DELETE FROM trade_cal WHERE cal_date > '20261013'")
        client = FakeClient()

        fetch_daily_basic(self.connection, client, today=TODAY)

        self.assertEqual([call["trade_date"] for call in client.calls], ["20261012", "20261013"])

    def test_every_trading_day_is_announced_before_its_request(self):
        timeline = []

        class Recorder(logging.Handler):
            def emit(self, record):
                timeline.append(("log", record.getMessage()))

        client = FakeClient()
        request = client.daily_basic

        def recording_request(**params):
            timeline.append(("fetch", params["trade_date"]))
            return request(**params)

        client.daily_basic = recording_request
        logger = logging.getLogger("screener.sync.daily_basic")
        recorder = Recorder()
        with self.assertLogs("screener.sync.daily_basic", level="INFO") as captured:
            logger.addHandler(recorder)
            try:
                fetch_daily_basic(self.connection, client, today=TODAY)
            finally:
                logger.removeHandler(recorder)

        self.assertEqual(len(captured.output), 5)
        self.assertEqual(timeline[0], ("log", "每日指标 第 1/5 个交易日：20261012"))
        self.assertEqual(timeline[1], ("fetch", "20261012"))
        self.assertEqual(timeline[-2], ("log", "每日指标 第 5/5 个交易日：20261016"))
        self.assertEqual(timeline[-1], ("fetch", "20261016"))
        self.assertEqual(len(timeline), 10)

    def test_null_values_are_stored_as_sql_null(self):
        fetch_daily_basic(self.connection, FakeClient([row("20261012", pe=None)]), today=TODAY)

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM daily_basic WHERE pe IS NULL").fetchone(),
            (1,),
        )

    def test_days_written_before_an_interruption_stay_on_disk(self):
        client = InterruptingClient([row("20261012"), row("20261013")], interrupt_on=3)

        with self.assertRaises(KeyboardInterrupt):
            fetch_daily_basic(self.connection, client, today=TODAY)

        reopened = sqlite3.connect(self.directory / "screener.sqlite3", timeout=0.2)
        try:
            self.assertEqual(
                [
                    record[0]
                    for record in reopened.execute(
                        "SELECT DISTINCT trade_date FROM daily_basic ORDER BY trade_date"
                    )
                ],
                ["20261012", "20261013"],
            )
        finally:
            reopened.close()
