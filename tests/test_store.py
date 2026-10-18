import shutil
import unittest
from pathlib import Path

from screener.store import (
    begin_sync,
    last_successful_sync_at,
    open_database,
    published_datasets,
)


class StoreVisibilityTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).parents[1] / "test-temp" / self.id().split(".")[-1]
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        self.path = self.directory / "screener.sqlite3"

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_no_publication_is_empty(self):
        connection = open_database(self.path)

        self.assertIsNone(last_successful_sync_at(connection))
        self.assertEqual(published_datasets(connection), frozenset())

    def test_aborted_batch_is_invisible(self):
        connection = open_database(self.path)
        first = begin_sync(connection)
        first.record("prices", 2)
        first.publish("2026-10-11 09:00:00")

        second = begin_sync(connection)
        second.record("financials", 4)
        second.abort()

        self.assertEqual(last_successful_sync_at(connection), "2026-10-11 09:00:00")
        self.assertEqual(published_datasets(connection), frozenset({"prices"}))

    def test_datasets_from_earlier_published_batches_stay_ready(self):
        connection = open_database(self.path)
        first = begin_sync(connection)
        first.record("prices", 2)
        first.publish("2026-10-11 09:00:00")

        second = begin_sync(connection)
        second.record("financials", 4)
        second.publish("2026-10-12 09:00:00")

        self.assertEqual(last_successful_sync_at(connection), "2026-10-12 09:00:00")
        self.assertEqual(published_datasets(connection), frozenset({"prices", "financials"}))
