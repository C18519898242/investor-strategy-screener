import shutil
import unittest
from pathlib import Path

from screener.store import last_successful_sync_at, open_database, published_datasets
from screener.sync import SyncError, sync_all


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
            env={"TUSHARE_TOKEN": "SECRET-TOKEN-VALUE"},
            fetch=lambda datasets, token: {"prices": 1},
            finished_at="2026-10-11 09:00:00",
        )

        def fail(datasets, token):
            raise RuntimeError("upstream failed")

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            sync_all(
                connection,
                datasets=("financials",),
                env={"TUSHARE_TOKEN": "SECRET-TOKEN-VALUE"},
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
                env={},
                fetch=lambda datasets, token: {},
                finished_at="2026-10-11 09:00:00",
            )

        self.assertIsNone(last_successful_sync_at(connection))
