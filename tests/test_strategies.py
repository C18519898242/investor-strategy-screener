import unittest

import pandas as pd

from screener.strategies import load_builtin_strategies


class StrategyRegistryTest(unittest.TestCase):
    def test_graham_registers_menu_and_output_without_formula(self):
        strategies = load_builtin_strategies()

        self.assertEqual(len(strategies), 1)
        spec = strategies[0].spec
        self.assertEqual(spec.strategy_id, "episode-01-graham")
        self.assertEqual(spec.menu_name, "格雷厄姆（可用）")
        self.assertEqual(spec.output_dir, "episode-01-graham")
        self.assertEqual(spec.required_datasets, ())
        self.assertEqual(spec.display_fields, ())
        with self.assertRaisesRegex(NotImplementedError, "第 1 集筛选尚未实现"):
            strategies[0].screen(pd.DataFrame())
