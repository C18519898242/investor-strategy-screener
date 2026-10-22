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
        # 排序脚本必须内联：页面允许本地脚本，但不能引用任何远程资源。
        self.assertNotIn("<script src", html)
        self.assertIn("<script>", html)
        for forbidden in ("pass", "fail", "insufficient_data"):
            self.assertNotIn(forbidden, html)

    def test_the_note_is_shown_next_to_the_count(self):
        html = render_html(
            strategy_name="格雷厄姆（可用）",
            trade_date="2026-10-16",
            synced_at="2026-10-23 00:09:14",
            rows=[{"code": "600519", "name": "贵州茅台", "roe": "36.02"}],
            display_fields=("roe",),
            note="近 3 年按新到旧排列：2025 / 2024 / 2023",
        )

        self.assertIn("近 3 年按新到旧排列：2025 / 2024 / 2023", html)
        self.assertIn('<span class="note">', html)

    def test_numeric_columns_are_right_aligned(self):
        html = render_html(
            strategy_name="格雷厄姆（可用）",
            trade_date="2026-10-16",
            synced_at=None,
            rows=[{"code": "600519", "name": "贵州茅台", "roe": "36.02"}],
            display_fields=("roe",),
        )

        self.assertIn('<td class="num">36.02</td>', html)
        self.assertIn('<th class="num">roe<span class="caret"></span></th>', html)

    def test_sort_keys_are_written_into_the_cell_that_can_be_sorted(self):
        html = render_html(
            strategy_name="格雷厄姆（可用）",
            trade_date="2026-10-16",
            synced_at=None,
            rows=[
                {"code": "600519", "name": "贵州茅台", "ROE（%）": "36.02 / 34.00 / 30.00"},
                {"code": "000858", "name": "五粮液", "ROE（%）": "25.00 / 24.00 / 23.00"},
            ],
            display_fields=("ROE（%）",),
            sort_values={"ROE（%）": [33.34, 24.0]},
        )

        self.assertIn('data-sort="33.340000"', html)
        self.assertIn('data-sort="24.000000"', html)

    def test_without_sort_keys_the_cell_carries_no_sort_attribute(self):
        html = render_html(
            strategy_name="格雷厄姆（可用）",
            trade_date="2026-10-16",
            synced_at=None,
            rows=[{"code": "600519", "name": "贵州茅台", "roe": "36.02"}],
            display_fields=("roe",),
        )

        self.assertNotIn("data-sort=", html)

    def test_same_timestamp_adds_sequence_without_overwrite(self):
        generated_at = dt.datetime(2026, 10, 11, 18, 5, 21)
        first = write_result(
            self.root, output_dir="episode-01-graham", generated_at=generated_at, html="first"
        )
        second = write_result(
            self.root, output_dir="episode-01-graham", generated_at=generated_at, html="second"
        )

        self.assertEqual(first.name, "screen-20261011-180521.html")
        self.assertEqual(second.name, "screen-20261011-180521-2.html")
        self.assertEqual(first.read_text(encoding="utf-8"), "first")
        self.assertEqual(second.read_text(encoding="utf-8"), "second")
