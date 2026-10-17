import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from screener import config
from screener.config import load_settings


class LoadSettingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.env_file = self.directory / ".env"
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TUSHARE_TOKEN", None)

    def test_default_env_file_is_repository_root(self):
        self.assertEqual(config.ENV_FILE, Path(__file__).parents[1] / ".env")

    def test_reads_token_from_env_file(self):
        self.env_file.write_text("TUSHARE_TOKEN=FROM-ENV-FILE\n", encoding="utf-8")

        settings = load_settings(self.env_file)

        self.assertEqual(settings.tushare_token, "FROM-ENV-FILE")

    def test_reads_api_url_from_env_file(self):
        self.env_file.write_text(
            "TUSHARE_TOKEN=FROM-ENV-FILE\nTUSHARE_API_URL=https://proxy.test/sdk\n",
            encoding="utf-8",
        )

        settings = load_settings(self.env_file)

        self.assertEqual(settings.tushare_api_url, "https://proxy.test/sdk")

    def test_environment_variable_wins_over_env_file(self):
        self.env_file.write_text("TUSHARE_TOKEN=FROM-ENV-FILE\n", encoding="utf-8")
        os.environ["TUSHARE_TOKEN"] = "FROM-ENVIRONMENT"

        settings = load_settings(self.env_file)

        self.assertEqual(settings.tushare_token, "FROM-ENVIRONMENT")

    def test_unrelated_env_file_keys_are_ignored(self):
        self.env_file.write_text(
            "# comment\nSOMETHING_ELSE=1\nTUSHARE_TOKEN=FROM-ENV-FILE\n",
            encoding="utf-8",
        )

        settings = load_settings(self.env_file)

        self.assertEqual(settings.tushare_token, "FROM-ENV-FILE")

    def test_missing_env_file_yields_empty_token(self):
        settings = load_settings(self.directory / "missing.env")

        self.assertEqual(settings.tushare_token, "")
