import shutil
import unittest
from pathlib import Path

import pandas as pd

from screener.store import insert_rows, open_database
from screener.strategies import episode_01_graham as graham

TRADE_DATE = "20261016"
# 从新到旧；最新的 20260930 只披露了零星几家，筛选时不能拿它当基准。
QUARTERS = [
    "20260930",
    "20260630",
    "20260331",
    "20251231",
    "20250930",
    "20250630",
    "20250331",
    "20241231",
    "20240930",
    "20240630",
    "20240331",
    "20231231",
    "20230930",
    "20230630",
    "20230331",
    "20221231",
]
USABLE = [period for period in QUARTERS if period <= "20260630"]
ANNUALS = ["20251231", "20241231", "20231231"]

MARKET_CAP_WAN = 1_000_000.0  # 100 亿元
EQUITY_YUAN = MARKET_CAP_WAN * 10_000 / 0.8  # 市净率 0.8
QUARTER_PROFIT_YUAN = MARKET_CAP_WAN * 10_000 / 10 / 4  # 市盈率 10


class ReferencePeriodTest(unittest.TestCase):
    """报告季没走完的那一期不能当基准，否则绝大多数股票会被判成缺数据。"""

    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _seed(self, coverage: dict[str, int]):
        insert_rows(
            self.connection,
            table="income",
            columns=("ts_code", "end_date", "report_type", "n_income_attr_p"),
            rows=[
                (f"0000{index}0.SZ", period, "2", 1.0)
                for period, count in coverage.items()
                for index in range(count)
            ],
        )

    def test_a_barely_reported_season_is_skipped(self):
        self._seed({"20260930": 1, "20260630": 5, "20260331": 5})

        self.assertEqual(graham._reference_period(self.connection, TRADE_DATE), "20260630")

    def test_a_fully_reported_season_is_used(self):
        self._seed({"20260930": 5, "20260630": 5})

        self.assertEqual(graham._reference_period(self.connection, TRADE_DATE), "20260930")

    def test_an_empty_income_table_falls_back_to_the_screening_day(self):
        self.assertEqual(graham._reference_period(self.connection, TRADE_DATE), TRADE_DATE)


class RatioTest(unittest.TestCase):
    def test_a_normal_ratio_is_compared_directly(self):
        result = graham._ratio_above(pd.Series([6.0]), pd.Series([2.0]), 2.0)

        self.assertTrue(bool(result.iloc[0]))

    def test_a_zero_denominator_passes_when_the_numerator_is_positive(self):
        result = graham._ratio_above(pd.Series([1.0]), pd.Series([0.0]), 2.0)

        self.assertTrue(bool(result.iloc[0]))

    def test_a_zero_denominator_fails_when_the_numerator_is_not_positive(self):
        result = graham._ratio_above(pd.Series([0.0]), pd.Series([0.0]), 2.0)

        self.assertFalse(bool(result.iloc[0]))

    def test_a_negative_denominator_is_excluded(self):
        result = graham._ratio_above(pd.Series([10.0]), pd.Series([-1.0]), 2.0)

        self.assertFalse(bool(result.iloc[0]))

    def test_a_missing_value_is_excluded(self):
        result = graham._ratio_above(pd.Series([None], dtype="float64"), pd.Series([2.0]), 2.0)

        self.assertFalse(bool(result.iloc[0]))


class AnnualProfitTest(unittest.TestCase):
    def test_four_quarters_add_up(self):
        matrix = pd.DataFrame(
            [{"20251231": 1.0, "20250930": 2.0, "20250630": 3.0, "20250331": 4.0}],
            index=["000001.SZ"],
        )

        yearly = graham._annual_profit(matrix)

        self.assertEqual(yearly.loc["000001.SZ", "2025"], 10.0)

    def test_a_missing_quarter_makes_the_year_unknown(self):
        matrix = pd.DataFrame(
            [{"20251231": 1.0, "20250930": 2.0, "20250630": 3.0}], index=["000001.SZ"]
        )

        yearly = graham._annual_profit(matrix)

        self.assertTrue(pd.isna(yearly.loc["000001.SZ", "2025"]))


class RollingProfitTest(unittest.TestCase):
    def test_the_rolling_total_needs_all_four_quarters(self):
        matrix = pd.DataFrame(
            [{"20250331": 1.0, "20241231": 2.0, "20240930": 3.0, "20240630": 4.0}],
            index=["000001.SZ"],
        )

        ttm = graham._rolling_profit(matrix, 1)

        self.assertEqual(ttm.loc["000001.SZ", "ttm0"], 10.0)

    def test_one_missing_quarter_gives_nan(self):
        matrix = pd.DataFrame(
            [{"20250331": 1.0, "20241231": 2.0, "20240930": 3.0}], index=["000001.SZ"]
        )

        ttm = graham._rolling_profit(matrix, 1)

        self.assertTrue(pd.isna(ttm.loc["000001.SZ", "ttm0"]))


class ScreenTest(unittest.TestCase):
    """在一个只有两只股票的小库上跑完整筛选。"""

    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.connection = open_database(self.directory / "screener.sqlite3")
        self._seed()

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def _seed(self):
        insert_rows(
            self.connection,
            table="stock_basic",
            columns=("ts_code", "name", "market", "industry"),
            rows=[
                ("000001.SZ", "通过股", "主板", "家用电器"),
                ("000002.SZ", "高流动负债", "主板", "房地产"),
                ("000003.SZ", "银行股", "主板", "银行"),
            ],
        )
        dates = sorted(set(USABLE) | set(ANNUALS) | {TRADE_DATE})
        insert_rows(
            self.connection,
            table="trade_cal",
            columns=("exchange", "cal_date", "is_open"),
            rows=[("SSE", date, 1) for date in dates],
        )
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=("ts_code", "trade_date", "total_mv"),
            rows=[
                (code, date, MARKET_CAP_WAN)
                for code in ("000001.SZ", "000002.SZ")
                for date in dates
            ],
        )
        insert_rows(
            self.connection,
            table="income",
            columns=("ts_code", "end_date", "report_type", "n_income_attr_p"),
            rows=[
                (code, period, "2", QUARTER_PROFIT_YUAN)
                for code in ("000001.SZ", "000002.SZ")
                for period in USABLE
            ],
        )
        insert_rows(
            self.connection,
            table="balancesheet",
            columns=(
                "ts_code",
                "end_date",
                "total_cur_assets",
                "total_cur_liab",
                "total_liab",
                "total_hldr_eqy_exc_min_int",
            ),
            rows=[
                ("000001.SZ", "20260630", 5e9, 1e9, 2e9, EQUITY_YUAN),
                ("000002.SZ", "20260630", 1.5e9, 1e9, 2e9, EQUITY_YUAN),
            ],
        )
        insert_rows(
            self.connection,
            table="fina_indicator",
            columns=("ts_code", "end_date", "roe_waa"),
            rows=[
                (code, period, 12.5) for code in ("000001.SZ", "000002.SZ") for period in ANNUALS
            ],
        )
        insert_rows(
            self.connection,
            table="dividend",
            columns=("ts_code", "end_date", "div_proc", "cash_div_tax", "base_share"),
            rows=[
                (code, f"{year}1231", "实施", 2.0, 10000.0)
                for code in ("000001.SZ", "000002.SZ")
                for year in (2023, 2024, 2025)
            ],
        )

    def test_only_the_stock_meeting_every_condition_is_returned(self):
        rows = graham.screen(self.connection, TRADE_DATE)

        self.assertEqual(list(rows["code"]), ["000001.SZ"])

    def test_the_displayed_numbers_follow_the_agreed_units(self):
        frame = graham.screen(self.connection, TRADE_DATE)
        row = frame.iloc[0].to_dict()

        self.assertEqual(row["格雷厄姆数"], "8.00")
        self.assertEqual(row["市盈率（TTM）"], "10.00")
        self.assertEqual(row["市净率"], "0.80")
        self.assertEqual(row["平均市盈率"], "10.00")
        self.assertEqual(row["ROE（%）"], "12.50 / 12.50 / 12.50")
        self.assertEqual(row["归母净利润（亿元）"], "10.00 / 10.00 / 10.00")
        self.assertEqual(row["股息率（%）"], "2.00 / 2.00 / 2.00")
        self.assertEqual(row["流动比率"], "5.00")
        self.assertEqual(row["营运资本/长期负债"], "4.00")
        self.assertEqual(row["总市值（亿元）"], "100.00")
        self.assertEqual(
            frame.attrs["note"],
            "ROE、归母净利润、股息率是近 3 年，按新到旧排列：2025 / 2024 / 2023；"
            "平均市盈率是近 12 个季度样本的均值；点列头排序时这三列按三年均值比",
        )

    def test_three_year_columns_sort_by_their_mean(self):
        frame = graham.screen(self.connection, TRADE_DATE)
        keys = frame.attrs["sort_values"]

        self.assertEqual(keys["格雷厄姆数"], [8.0])
        self.assertEqual(keys["ROE（%）"], [12.5])
        self.assertEqual(keys["归母净利润（亿元）"], [10.0])
        self.assertEqual(keys["股息率（%）"], [2.0])
        self.assertEqual(keys["总市值（亿元）"], [100.0])

    def test_banks_are_not_in_the_pool(self):
        codes = set(graham.evaluate(self.connection, TRADE_DATE)["code"])

        self.assertNotIn("000003.SZ", codes)

    def test_a_stock_suspended_on_the_screening_day_is_excluded(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=("ts_code", "trade_date", "suspend_type", "suspend_timing"),
            rows=[("000001.SZ", TRADE_DATE, "S", "09:30-15:00")],
        )

        self.assertTrue(graham.screen(self.connection, TRADE_DATE).empty)

    def test_the_report_season_that_has_not_closed_is_ignored(self):
        """20260930 已经有零星披露，但不能拿它当基准。"""
        insert_rows(
            self.connection,
            table="income",
            columns=("ts_code", "end_date", "report_type", "n_income_attr_p"),
            rows=[("000001.SZ", "20260930", "2", QUARTER_PROFIT_YUAN)],
        )

        rows = graham.screen(self.connection, TRADE_DATE)

        self.assertEqual(list(rows["code"]), ["000001.SZ"])

    def test_an_empty_database_returns_nothing(self):
        connection = open_database(self.directory / "empty.sqlite3")

        self.assertTrue(graham.screen(connection, TRADE_DATE).empty)
        connection.close()


if __name__ == "__main__":
    unittest.main()
