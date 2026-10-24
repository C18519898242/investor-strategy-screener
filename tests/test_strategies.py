import unittest

from screener.strategies import load_builtin_strategies
from screener.strategies.episode_01_graham import DISPLAY_FIELDS, GrahamStrategy
from screener.strategies.episode_04_buffett import BuffettStrategy


class StrategyRegistryTest(unittest.TestCase):
    def test_graham_registers_the_menu_entry_and_the_datasets_it_reads(self):
        strategies = load_builtin_strategies()

        self.assertEqual(len(strategies), 2)
        spec = strategies[0].spec
        self.assertEqual(spec.strategy_id, "episode-01-graham")
        self.assertEqual(spec.menu_name, "格雷厄姆（可用）")
        self.assertEqual(spec.output_dir, "episode-01-graham")
        self.assertEqual(
            spec.required_datasets,
            (
                "股票列表",
                "交易日历",
                "停复牌",
                "每日指标",
                "利润表",
                "资产负债表",
                "财务指标",
                "分红送配",
            ),
        )
        self.assertEqual(spec.display_fields, DISPLAY_FIELDS)

    def test_buffett_registers_the_menu_entry_and_the_datasets_it_reads(self):
        spec = load_builtin_strategies()[1].spec

        self.assertEqual(spec.strategy_id, "episode-04-buffett")
        self.assertEqual(spec.menu_name, "巴菲特（可用）")
        self.assertEqual(spec.output_dir, "episode-04-buffett")
        self.assertEqual(
            spec.required_datasets,
            (
                "股票列表",
                "交易日历",
                "停复牌",
                "每日指标",
                "利润表",
                "资产负债表",
                "财务指标",
                "分红送配",
                "现金流量表",
            ),
        )

    def test_the_registered_strategies_screen_from_the_database(self):
        """菜单拿到的是可调用的筛选函数，不是占位实现。"""
        self.assertTrue(callable(GrahamStrategy().screen))
        self.assertTrue(callable(BuffettStrategy().screen))


if __name__ == "__main__":
    unittest.main()
