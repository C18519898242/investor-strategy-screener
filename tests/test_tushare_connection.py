import sys
import types
import unittest
from pathlib import Path

from screener.config import Settings
from screener.sync import SyncError, create_tushare_client

PROXY_URL = "https://tl.kaixin8.top/tushare/sdk"


def settings(
    tushare_token: str = "TEST-TOKEN-NOT-REAL",
    tushare_api_url: str = PROXY_URL,
) -> Settings:
    """隔离的配置对象：不读本机 .env，值只由测试代码给定。"""
    return Settings(
        _env_file=None,
        tushare_token=tushare_token,
        tushare_api_url=tushare_api_url,
    )


class FakeApi:
    pass


def install_fake_tushare(calls: list) -> None:
    fake = types.ModuleType("tushare")

    def pro_api(token):
        calls.append(token)
        return FakeApi()

    fake.pro_api = pro_api
    sys.modules["tushare"] = fake


class TushareConnectionTest(unittest.TestCase):
    def test_client_uses_configured_token_and_api_url(self):
        calls = []
        install_fake_tushare(calls)
        token = "TEST-TOKEN-NOT-REAL"
        api_url = "https://proxy.test/tushare/sdk"

        client = create_tushare_client(settings(token, api_url))

        self.assertEqual(calls, [token])
        self.assertEqual(client._DataApi__http_url, api_url)
        source = Path("screener/sync.py").read_text(encoding="utf-8")
        self.assertNotIn(token, source)

    def test_token_surrounding_whitespace_is_trimmed(self):
        calls = []
        install_fake_tushare(calls)

        create_tushare_client(settings(tushare_token="  PADDED-TOKEN  "))

        self.assertEqual(calls, ["PADDED-TOKEN"])

    def test_api_url_surrounding_whitespace_is_trimmed(self):
        install_fake_tushare([])

        client = create_tushare_client(settings(tushare_api_url="  https://proxy.test/sdk  "))

        self.assertEqual(client._DataApi__http_url, "https://proxy.test/sdk")

    def test_missing_token_raises_before_network(self):
        calls = []
        install_fake_tushare(calls)

        with self.assertRaisesRegex(
            SyncError, "缺少 TUSHARE_TOKEN，请在 .env 或环境变量中设置"
        ):
            create_tushare_client(settings(tushare_token=""))

        self.assertEqual(calls, [])

    def test_missing_api_url_raises_before_network(self):
        calls = []
        install_fake_tushare(calls)

        with self.assertRaisesRegex(
            SyncError, "缺少 TUSHARE_API_URL，请在 .env 或环境变量中设置"
        ):
            create_tushare_client(settings(tushare_api_url=""))

        self.assertEqual(calls, [])

    def test_missing_both_reports_both_in_one_error(self):
        calls = []
        install_fake_tushare(calls)

        with self.assertRaisesRegex(
            SyncError,
            "缺少 TUSHARE_TOKEN、TUSHARE_API_URL，请在 .env 或环境变量中设置",
        ):
            create_tushare_client(settings(tushare_token="", tushare_api_url=""))

        self.assertEqual(calls, [])
