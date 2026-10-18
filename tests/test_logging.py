import logging
import shutil
import unittest
from pathlib import Path

from screener.logging import setup_logging, shutdown_logging


class LoggingTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.log_file = self.root / "logs" / "screener.log"
        self.console = []

    def tearDown(self):
        shutdown_logging()
        shutil.rmtree(self.root, ignore_errors=True)

    def _setup(self, log_file=None):
        setup_logging(
            log_file=self.log_file if log_file is None else log_file,
            console=self.console.append,
        )

    def test_writes_one_record_to_both_sinks(self):
        self._setup()

        logging.getLogger("screener.sync.registry").info("开始拉取 股票列表")

        self.assertEqual(self.console, ["开始拉取 股票列表"])
        text = self.log_file.read_text(encoding="utf-8")
        self.assertIn("INFO", text)
        self.assertIn("screener.sync.registry", text)
        self.assertIn("开始拉取 股票列表", text)

    def test_debug_reaches_the_file_but_not_the_console(self):
        self._setup()

        logging.getLogger("screener.sync.retry").debug("第 2 次尝试成功")

        self.assertEqual(self.console, [])
        self.assertIn("第 2 次尝试成功", self.log_file.read_text(encoding="utf-8"))

    def test_traceback_goes_to_the_file_only(self):
        self._setup()

        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("screener.cli").error("更新失败：boom", exc_info=True)

        self.assertEqual(self.console, ["更新失败：boom"])
        text = self.log_file.read_text(encoding="utf-8")
        self.assertIn("Traceback", text)
        self.assertIn("ValueError: boom", text)

    def test_setting_up_twice_does_not_accumulate_handlers(self):
        self._setup()
        self._setup()

        self.assertEqual(len(logging.getLogger("screener").handlers), 2)

        logging.getLogger("screener.cli").info("启动：投资人选股策略")

        self.assertEqual(self.console, ["启动：投资人选股策略"])

    def test_creates_the_log_directory(self):
        self.assertFalse(self.log_file.parent.exists())

        self._setup()

        self.assertTrue(self.log_file.parent.is_dir())

    def test_appends_instead_of_truncating(self):
        self._setup()
        logging.getLogger("screener.cli").info("第一次")
        self._setup()
        logging.getLogger("screener.cli").info("第二次")

        text = self.log_file.read_text(encoding="utf-8")

        self.assertIn("第一次", text)
        self.assertIn("第二次", text)

    def test_a_directory_as_log_file_is_not_swallowed(self):
        with self.assertRaises(OSError):
            setup_logging(log_file=self.root, console=self.console.append)
