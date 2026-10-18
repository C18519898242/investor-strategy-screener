import shutil
import unittest
from pathlib import Path

import pandas as pd

from screener.cli import main
from screener.config import Settings
from screener.store import begin_sync, open_database
from screener.strategies.base import Strategy, StrategySpec

PROXY_URL = "https://tl.kaixin8.top/tushare/sdk"


def settings(
    tushare_token: str = "SECRET-TOKEN-VALUE",
    tushare_api_url: str = PROXY_URL,
) -> Settings:
    """隔离的配置对象：不读本机 .env，值只由测试代码给定。"""
    return Settings(
        _env_file=None,
        tushare_token=tushare_token,
        tushare_api_url=tushare_api_url,
    )


class ReadyStrategy(Strategy):
    spec = StrategySpec(
        strategy_id="ready",
        menu_name="测试策略",
        output_dir="ready",
        required_datasets=("prices", "financials"),
        display_fields=(),
    )

    def screen(self, frame: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame()


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.connection = open_database(self.root / "screener.sqlite3")
        self.output = []
        self.fetch_calls = 0

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, answers, config=None):
        values = iter(answers)

        def answer(prompt=""):
            try:
                return next(values)
            except StopIteration:
                return "0"

        return main(
            input_fn=answer,
            output_fn=self.output.append,
            connection=self.connection,
            settings=settings() if config is None else config,
            fetch=self._fetch,
            clock=lambda: "2026-10-11 09:00:00",
            strategies=(ReadyStrategy(),),
            output_root=self.root,
        )

    def _fetch(self, connection, datasets, client):
        self.fetch_calls += 1
        return {dataset: 3 for dataset in datasets}

    def test_blank_trade_date_reports_missing_calendar(self):
        self._run(["2", "1", ""])

        self.assertIn("本地交易日历尚未就绪", "\n".join(self.output))
        self.assertEqual(self.fetch_calls, 0)
        self.assertFalse((self.root / "outputs").exists())

    def test_partial_data_does_not_create_html(self):
        batch = begin_sync(self.connection)
        batch.record("prices", 3)
        batch.publish("2026-10-11 09:00:00")
        self.output.clear()

        self._run(["2", "1", "2026-10-10"])

        self.assertIn("本地数据不完整，无法筛选", "\n".join(self.output))
        self.assertFalse((self.root / "outputs").exists())

    def test_successful_sync_shows_time_without_token(self):
        code = self._run(["1", "1", ""])

        self.assertEqual(code, 0)
        text = "\n".join(self.output)
        self.assertIn("2026-10-11 09:00:00", text)
        self.assertNotIn("SECRET-TOKEN-VALUE", text)

    def test_one_run_reports_every_missing_config_name(self):
        code = self._run(["1", "1", ""], config=settings(tushare_token="", tushare_api_url=""))

        self.assertEqual(code, 1)
        text = "\n".join(self.output)
        self.assertIn("缺少 TUSHARE_TOKEN、TUSHARE_API_URL", text)
        self.assertEqual(self.fetch_calls, 0)

    def test_one_run_reports_the_single_missing_name(self):
        code = self._run(["1", "1", ""], config=settings(tushare_api_url=""))

        self.assertEqual(code, 1)
        text = "\n".join(self.output)
        self.assertIn("缺少 TUSHARE_API_URL，请在 .env 或环境变量中设置", text)
        self.assertNotIn("TUSHARE_TOKEN", text)
