import shutil
import unittest
from pathlib import Path

from screener.store import insert_rows, open_database
from screener.strategies import episode_04_buffett as buffett

TRADE_DATE = "20261016"
YEARS = ["2025", "2024", "2023", "2022", "2021"]
PERIODS = [f"{year}{suffix}" for year in YEARS for suffix in ("0331", "0630", "0930", "1231")]
ANNUALS = [f"{year}1231" for year in YEARS]

MARKET_CAP_WAN = 1_000_000.0  # 100 亿元
QUARTER_PROFIT = 25_000_000.0  # 一年 1 亿元
QUARTER_REVENUE = 100_000_000.0  # 一年 4 亿元
QUARTER_OPERATING = 30_000_000.0  # 一年 1.2 亿元
QUARTER_CAPEX = 5_000_000.0  # 一年 0.2 亿元 → 营收的 5%
DIVIDEND_PER_SHARE = 0.5
BASE_SHARE = 10000.0  # 万股 → 每股 0.5 元 × 1 亿股 = 5,000 万元 = 50% 派息率
EQUITY_YUAN = 2_000_000_000.0  # 20 亿元净资产 → 市净率 5
EPS = {"2025": 2.0, "2024": 1.8, "2023": 1.7, "2022": 1.6, "2021": 1.5}


class BuffetStrategyTest(unittest.TestCase):
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
                ("000001.SZ", "优质股", "主板", "家用电器"),
                ("000002.SZ", "银行股", "主板", "银行"),
            ],
        )
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=("ts_code", "trade_date", "total_mv"),
            rows=[("000001.SZ", TRADE_DATE, MARKET_CAP_WAN)],
        )
        self._seed_statements("000001.SZ")
        self._seed_indicators("000001.SZ", roic=25.0)
        self._seed_dividends("000001.SZ")
        self._seed_equity("000001.SZ")

    def _seed_equity(self, code, equity=EQUITY_YUAN):
        insert_rows(
            self.connection,
            table="balancesheet",
            columns=("ts_code", "end_date", "report_type", "total_hldr_eqy_exc_min_int"),
            rows=[(code, "20251231", "1", equity)],
        )

    def _seed_statements(self, code, *, capex=QUARTER_CAPEX, revenue=QUARTER_REVENUE):
        insert_rows(
            self.connection,
            table="income",
            columns=("ts_code", "end_date", "report_type", "n_income_attr_p", "revenue"),
            rows=[(code, period, "2", QUARTER_PROFIT, revenue) for period in PERIODS],
        )
        insert_rows(
            self.connection,
            table="cashflow",
            columns=(
                "ts_code",
                "end_date",
                "report_type",
                "n_cashflow_act",
                "c_pay_acq_const_fiolta",
            ),
            rows=[(code, period, "2", QUARTER_OPERATING, capex) for period in PERIODS],
        )

    def _seed_indicators(self, code, *, roic=25.0, roe=20.0, years=None):
        insert_rows(
            self.connection,
            table="fina_indicator",
            columns=("ts_code", "end_date", "roe_waa", "roic", "eps"),
            rows=[
                (code, f"{year}1231", roe, roic, EPS[year])
                for year in (YEARS if years is None else years)
            ],
        )

    def _seed_dividends(self, code, *, amount=DIVIDEND_PER_SHARE, years=None):
        insert_rows(
            self.connection,
            table="dividend",
            columns=("ts_code", "end_date", "div_proc", "cash_div_tax", "base_share"),
            rows=[
                (code, f"{year}1231", "实施", amount, BASE_SHARE)
                for year in (YEARS if years is None else years)
            ],
        )

    def _codes(self):
        return list(buffett.screen(self.connection, TRADE_DATE)["code"])

    def test_a_stock_meeting_every_condition_is_returned(self):
        self.assertEqual(self._codes(), ["000001.SZ"])

    def test_banks_are_not_in_the_pool(self):
        self._seed_statements("000002.SZ")
        self._seed_indicators("000002.SZ")
        self._seed_dividends("000002.SZ")
        insert_rows(
            self.connection,
            table="daily_basic",
            columns=("ts_code", "trade_date", "total_mv"),
            rows=[("000002.SZ", TRADE_DATE, MARKET_CAP_WAN)],
        )

        self.assertEqual(self._codes(), ["000001.SZ"])

    def test_a_stock_suspended_on_the_screening_day_is_excluded(self):
        insert_rows(
            self.connection,
            table="suspend_d",
            columns=("ts_code", "trade_date", "suspend_type", "suspend_timing"),
            rows=[("000001.SZ", TRADE_DATE, "S", "09:30-15:00")],
        )

        self.assertTrue(buffett.screen(self.connection, TRADE_DATE).empty)

    def test_low_roe_is_excluded(self):
        self.connection.execute(
            "UPDATE fina_indicator SET roe_waa = 10.0 WHERE end_date = '20251231'"
        )

        self.assertEqual(self._codes(), [])

    def test_a_roic_that_is_too_high_counts_as_an_anomaly(self):
        """分母趋零算出的假高值不是优质，按异常排除。"""
        self.connection.execute("DELETE FROM fina_indicator")
        self._seed_indicators("000001.SZ", roic=150.0)

        self.assertEqual(self._codes(), [])

    def test_high_capex_is_excluded(self):
        self.connection.execute("DELETE FROM cashflow")
        self._seed_statements("000001.SZ", capex=30_000_000.0)  # 营收的 30%

        self.assertEqual(self._codes(), [])

    def test_a_low_payout_year_is_excluded(self):
        self.connection.execute("DELETE FROM dividend")
        self._seed_dividends("000001.SZ", amount=0.05)  # 派息率 5%

        self.assertEqual(self._codes(), [])

    def test_a_year_without_any_dividend_row_counts_as_zero(self):
        """库里没有记录不是数据缺失，按 0 计，该年不达标。"""
        self.connection.execute("DELETE FROM dividend WHERE end_date LIKE '2023%'")

        self.assertEqual(self._codes(), [])

    def test_a_missing_annual_eps_makes_the_growth_unknown(self):
        self.connection.execute("DELETE FROM fina_indicator WHERE end_date = '20211231'")

        self.assertEqual(self._codes(), [])

    def test_a_missing_quarter_makes_the_year_unknown(self):
        self.connection.execute("DELETE FROM cashflow WHERE end_date = '20250630'")

        self.assertEqual(self._codes(), [])

    def test_the_displayed_numbers_follow_the_agreed_units(self):
        frame = buffett.screen(self.connection, TRADE_DATE)
        row = frame.iloc[0].to_dict()

        self.assertEqual(row["ROE（%）"], "20.00")
        self.assertEqual(row["三年平均 ROE（%）"], "20.00")
        self.assertEqual(row["ROIC（%）"], "25.00")
        self.assertEqual(row["自由现金流净现比"], "1.00")
        self.assertEqual(row["资本支出比率（%）"], "5.00")
        self.assertEqual(row["EPS 五年复合增速（%）"], "7.46")
        self.assertEqual(row["派息率（%）"], "50.00 / 50.00 / 50.00 / 50.00 / 50.00")
        self.assertEqual(row["总市值（亿元）"], "100.00")
        self.assertEqual(row["市盈率（TTM）"], "100.00")
        self.assertEqual(row["市净率"], "5.00")

    def test_pe_and_pb_are_reference_only(self):
        """参考列缺数据不影响入选：删掉资产负债表，股票仍然通过。"""
        self.connection.execute("DELETE FROM balancesheet")

        frame = buffett.screen(self.connection, TRADE_DATE)

        self.assertEqual(list(frame["code"]), ["000001.SZ"])
        self.assertEqual(frame.iloc[0]["市净率"], "—")
        self.assertIsNone(frame.attrs["sort_values"]["市净率"][0])

    def test_a_negative_equity_shows_no_pb(self):
        self.connection.execute("UPDATE balancesheet SET total_hldr_eqy_exc_min_int = -1000.0")

        frame = buffett.screen(self.connection, TRADE_DATE)

        self.assertEqual(frame.iloc[0]["市净率"], "—")
        self.assertIsNone(frame.attrs["sort_values"]["市净率"][0])

    def test_the_note_names_the_years_and_the_sorting_rule(self):
        frame = buffett.screen(self.connection, TRADE_DATE)

        self.assertEqual(
            frame.attrs["note"],
            "ROE 取 2025 年报；三年平均 ROE 是 2025 / 2024 / 2023 的均值；"
            "派息率是 2025 / 2024 / 2023 / 2022 / 2021 每年，按新到旧排列；"
            "点列头排序时派息率按五年均值比；市盈率、市净率只作参考，不参与筛选",
        )

    def test_every_column_has_a_sort_key(self):
        keys = buffett.screen(self.connection, TRADE_DATE).attrs["sort_values"]

        self.assertEqual(set(keys), set(buffett.DISPLAY_FIELDS))
        self.assertEqual(keys["ROE（%）"], [20.0])
        self.assertEqual(keys["三年平均 ROE（%）"], [20.0])
        self.assertEqual(keys["资本支出比率（%）"], [5.0])
        self.assertEqual(keys["派息率（%）"], [0.5])
        self.assertEqual(keys["市盈率（TTM）"], [100.0])
        self.assertEqual(keys["市净率"], [5.0])

    def test_an_empty_database_returns_nothing(self):
        connection = open_database(self.directory / "empty.sqlite3")

        self.assertTrue(buffett.screen(connection, TRADE_DATE).empty)
        connection.close()


if __name__ == "__main__":
    unittest.main()
