import sys
import types
import unittest
from pathlib import Path

from screener.sync import SyncError, create_tushare_client


class FakeApi:
    pass


class TushareConnectionTest(unittest.TestCase):
    def test_client_uses_environment_token_and_proxy(self):
        calls = []
        fake = types.ModuleType("tushare")

        def pro_api(token):
            calls.append(token)
            return FakeApi()

        fake.pro_api = pro_api
        sys.modules["tushare"] = fake
        token = "TEST-TOKEN-NOT-REAL"

        client = create_tushare_client({"TUSHARE_TOKEN": token})

        self.assertEqual(calls, [token])
        self.assertEqual(client._DataApi__http_url, "https://tl.kaixin8.top/tushare/sdk")
        source = Path("screener/sync.py").read_text(encoding="utf-8")
        self.assertNotIn(token, source)

    def test_missing_token_raises_before_network(self):
        with self.assertRaisesRegex(SyncError, "缺少环境变量 TUSHARE_TOKEN"):
            create_tushare_client({})
