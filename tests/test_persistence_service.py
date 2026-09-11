import tempfile
import unittest
from pathlib import Path

from sentinel.application.persistence_service import PersistentSnapshotService
from tests.test_snapshot_repository import collection


class PersistenceServiceTests(unittest.TestCase):
    def test_records_controlled_collection_to_explicit_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            stored = PersistentSnapshotService(path).record_collection(collection(partial_processes=True))
            self.assertEqual(stored.id, 1)
            self.assertEqual(stored.collection.snapshot.processes[0].pid, 42)
            self.assertTrue(path.exists())
