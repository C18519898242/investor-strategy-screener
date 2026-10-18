import shutil
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from screener.cli import _parse_skips, main
from screener.config import Settings
from screener.logging import shutdown_logging
from screener.store import begin_sync, open_database, published_datasets
from screener.strategies.base import Strategy, StrategySpec
from screener.strategies.episode_01_graham import GrahamStrategy
from tests.tushare_fakes import FakeTushareClient, stock_basic_row

PROXY_URL = "https://tl.kaixin8.top/tushare/sdk"

TRADE_CAL_ROWS = [{"exchange": "SSE", "cal_date": "20261016", "is_open": 1, "pretrade_date": ""}]


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
        self.log_file = self.root / "logs" / "screener.log"
        self.output = []
        self.fetch_calls = 0
        self.fetched = []
        self.prompts = []

    def tearDown(self):
        shutdown_logging()
        self.connection.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, answers, config=None):
        values = iter(answers)

        def answer(prompt=""):
            self.prompts.append(prompt)
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
            log_file=self.log_file,
        )

    def _fetch(self, connection, datasets, client):
        self.fetch_calls += 1
        self.fetched.append(datasets)
        return {dataset: 3 for dataset in datasets}

    def test_blank_answer_fetches_every_dataset(self):
        self._run(["1", ""])

        self.assertIn("直接回车＝全都补", "\n".join(self.prompts))
        self.assertEqual(self.fetched, [("prices", "financials")])

    def test_skip_number_leaves_that_dataset_out(self):
        self._run(["1", "2", ""])

        self.assertEqual(self.fetched, [("prices",)])

    def test_skip_accepts_spaces_commas_and_full_width_commas(self):
        self._run(["1", "2，", ""])

        self.assertEqual(self.fetched, [("prices",)])

    def test_invalid_skip_number_is_asked_again(self):
        self._run(["1", "9", "1", ""])

        self.assertEqual(self.fetched, [("financials",)])
        self.assertIn("编号应为 0~2 之间的数字", "\n".join(self.output))

    def test_skipping_every_dataset_fetches_nothing(self):
        code = self._run(["1", "1 2"])

        self.assertEqual(code, 0)
        self.assertEqual(self.fetched, [])
        self.assertIn("没有要更新的数据集", "\n".join(self.output))

    def test_the_checklist_names_every_dataset(self):
        self._run(["1", ""])

        text = "\n".join(self.output)
        self.assertIn("本次要更新 2 个数据集", text)
        self.assertIn("  1. prices（未登记）", text)
        self.assertIn("  2. financials（未登记）", text)

    def test_the_main_menu_goes_straight_to_the_checklist(self):
        self._run(["1", ""])

        self.assertEqual(
            self.prompts,
            [
                "请选择：",
                "不补哪几个（编号用空格或逗号分隔；直接回车＝全都补；0＝返回主菜单）：",
                "按回车返回主菜单",
                "请选择：",
            ],
        )

    def test_zero_at_the_checklist_returns_to_the_main_menu_without_fetching(self):
        code = self._run(["1", "0"])

        self.assertEqual(
            self.prompts[1],
            "不补哪几个（编号用空格或逗号分隔；直接回车＝全都补；0＝返回主菜单）：",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.fetched, [])
        self.assertEqual(self.output.count("1. 更新本地数据"), 2)

    def test_zero_wins_over_other_numbers(self):
        self.assertIsNone(_parse_skips("0 2", 3))

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
        code = self._run(["1", ""])

        self.assertEqual(code, 0)
        text = "\n".join(self.output)
        self.assertIn("2026-10-11 09:00:00", text)
        self.assertNotIn("SECRET-TOKEN-VALUE", text)

    def test_one_run_reports_every_missing_config_name(self):
        code = self._run(["1", ""], config=settings(tushare_token="", tushare_api_url=""))

        self.assertEqual(code, 1)
        text = "\n".join(self.output)
        self.assertIn("缺少 TUSHARE_TOKEN、TUSHARE_API_URL", text)
        self.assertEqual(self.fetch_calls, 0)

    def test_one_run_reports_the_single_missing_name(self):
        code = self._run(["1", ""], config=settings(tushare_api_url=""))

        self.assertEqual(code, 1)
        text = "\n".join(self.output)
        self.assertIn("缺少 TUSHARE_API_URL，请在 .env 或环境变量中设置", text)
        self.assertNotIn("TUSHARE_TOKEN", text)

    def test_update_uses_the_real_fetcher_by_default(self):
        values = iter(["1", ""])

        def answer(prompt=""):
            try:
                return next(values)
            except StopIteration:
                return "0"

        client = FakeTushareClient(
            stock_basic_rows=[stock_basic_row(ts_code="000001.SZ", list_status="L")],
            trade_cal_rows=TRADE_CAL_ROWS,
        )

        with mock.patch(
            "screener.sync.pipeline.create_tushare_client",
            return_value=client,
        ):
            code = main(
                input_fn=answer,
                output_fn=self.output.append,
                connection=self.connection,
                settings=settings(),
                clock=lambda: "2026-10-11 09:00:00",
                strategies=(GrahamStrategy(),),
                output_root=self.root,
                log_file=self.log_file,
            )

        self.assertEqual(code, 0)
        self.assertEqual(published_datasets(self.connection), frozenset({"股票列表", "交易日历"}))
        self.assertNotIn("SECRET-TOKEN-VALUE", "\n".join(self.output))

    def test_log_file_never_contains_the_token(self):
        self._run(["1", ""])

        text = self.log_file.read_text(encoding="utf-8")

        self.assertIn("更新完成：2026-10-11 09:00:00", text)
        self.assertIn(f"客户端就绪：timeout=120 秒，地址 {PROXY_URL}", text)
        self.assertNotIn("SECRET-TOKEN-VALUE", text)

    def test_sync_logs_the_dataset_names(self):
        self._run(["1", ""])

        self.assertIn("更新范围：prices、financials", "\n".join(self.output))
