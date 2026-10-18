import unittest

import pandas as pd

from screener.strategies import load_builtin_strategies
from screener.sync.registry import DATASET_FETCHERS


class StrategyRegistryTest(unittest.TestCase):
    def test_graham_registers_menu_and_output_without_formula(self):
        strategies = load_builtin_strategies()

        self.assertEqual(len(strategies), 2)
        spec = strategies[0].spec
        self.assertEqual(spec.strategy_id, "episode-01-graham")
        self.assertEqual(spec.menu_name, "格雷厄姆（可用）")
        self.assertEqual(spec.output_dir, "episode-01-graham")
        self.assertEqual(spec.required_datasets, ("股票列表", "交易日历"))
        self.assertEqual(spec.display_fields, ())
        with self.assertRaisesRegex(NotImplementedError, "第 1 集筛选尚未实现"):
            strategies[0].screen(pd.DataFrame())

    def test_mock_strategy_registers_every_registered_dataset(self):
        strategies = {strategy.spec.strategy_id: strategy for strategy in load_builtin_strategies()}

        mock = strategies["mock-all-datasets"]
        self.assertEqual(mock.spec.menu_name, "【联调】全部数据集")
        self.assertEqual(mock.spec.output_dir, "mock-all-datasets")
        self.assertEqual(mock.spec.required_datasets, tuple(DATASET_FETCHERS))
        self.assertEqual(mock.spec.display_fields, ())
        with self.assertRaisesRegex(NotImplementedError, "联调用策略不做筛选"):
            mock.screen(pd.DataFrame())
