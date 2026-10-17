import shutil
import unittest
from pathlib import Path

from screener.config import Settings
from screener.store import last_successful_sync_at, open_database, published_datasets
from screener.sync import SyncError, sync_all

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


class SyncOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.path = self.directory / "screener.sqlite3"

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_failed_fetch_keeps_previous_publication(self):
        connection = open_database(self.path)
        sync_all(
            connection,
            datasets=("prices",),
            settings=settings(),
            fetch=lambda datasets, token: {"prices": 1},
            finished_at="2026-10-11 09:00:00",
        )

        def fail(datasets, token):
            raise RuntimeError("upstream failed")

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            sync_all(
                connection,
                datasets=("financials",),
                settings=settings(),
                fetch=fail,
                finished_at="2026-10-11 10:00:00",
            )

        self.assertEqual(published_datasets(connection), frozenset({"prices"}))
        self.assertNotIn("SECRET-TOKEN-VALUE", self.path.read_bytes().decode("utf-8", "ignore"))

    def test_missing_token_does_not_open_batch(self):
        connection = open_database(self.path)

        with self.assertRaises(SyncError):
            sync_all(
                connection,
                datasets=("prices",),
                settings=settings(tushare_token=""),
                fetch=lambda datasets, token: {},
                finished_at="2026-10-11 09:00:00",
            )

        self.assertIsNone(last_successful_sync_at(connection))

    def test_missing_api_url_does_not_open_batch(self):
        connection = open_database(self.path)

        with self.assertRaisesRegex(SyncError, "缺少 TUSHARE_API_URL"):
            sync_all(
                connection,
                datasets=("prices",),
                settings=settings(tushare_api_url=""),
                fetch=lambda datasets, token: {},
                finished_at="2026-10-11 09:00:00",
            )

        self.assertIsNone(last_successful_sync_at(connection))
