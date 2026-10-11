import datetime as dt
import shutil
import unittest
from pathlib import Path

from screener.report import render_html, write_result


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_result_states_no_stock_was_selected(self):
        html = render_html(
            strategy_name="格雷厄姆（可用）",
            trade_date="2026-10-10",
            synced_at="2026-10-11 09:00:00",
            rows=[],
            display_fields=(),
        )

        self.assertIn("本次无符合条件的股票", html)
        self.assertNotIn("600519", html)

    def test_selected_stock_shows_code_name_and_labels(self):
        html = render_html(
            strategy_name="格雷厄姆（可用）",
            trade_date="2026-10-10",
            synced_at=None,
            rows=[{"code": "600519", "name": "贵州茅台", "roe": "36.02"}],
            display_fields=("roe",),
        )

        self.assertIn("600519", html)
        self.assertIn("贵州茅台", html)
        self.assertIn("roe", html)
        self.assertIn("36.02", html)
        self.assertIn("本地数据上次更新：无", html)
        self.assertIn("<style>", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("<script", html)
        for forbidden in ("pass", "fail", "insufficient_data"):
            self.assertNotIn(forbidden, html)

    def test_same_timestamp_adds_sequence_without_overwrite(self):
        generated_at = dt.datetime(2026, 10, 11, 18, 5, 21)
        first = write_result(self.root, output_dir="episode-01-graham", generated_at=generated_at, html="first")
        second = write_result(self.root, output_dir="episode-01-graham", generated_at=generated_at, html="second")

        self.assertEqual(first.name, "screen-20261011-180521.html")
        self.assertEqual(second.name, "screen-20261011-180521-2.html")
        self.assertEqual(first.read_text(encoding="utf-8"), "first")
        self.assertEqual(second.read_text(encoding="utf-8"), "second")
