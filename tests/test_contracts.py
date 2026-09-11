from datetime import UTC, datetime
import sys
import unittest

from sentinel.application.snapshot_service import SnapshotService
from sentinel.models import CollectionResult, CollectionStatus


class ContractTests(unittest.TestCase):
    def test_success_requires_value(self) -> None:
        with self.assertRaises(ValueError):
            CollectionResult(None, CollectionStatus.SUCCESS, datetime.now(UTC), 0.0)

    def test_timestamp_must_be_utc(self) -> None:
        with self.assertRaises(ValueError):
            CollectionResult("ok", CollectionStatus.SUCCESS, datetime.now(), 0.0)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc integration")
    def test_snapshot_service_is_non_destructive_linux_smoke(self) -> None:
        result = SnapshotService().collect()
        self.assertIs(result.snapshot.timestamp.tzinfo, UTC)
        self.assertIsNotNone(result.snapshot.memory)
        self.assertIsNotNone(result.snapshot.cpu)
        self.assertIsNotNone(result.snapshot.system)
        self.assertIn(dict(result.snapshot.results)["processes"], {"success", "partial"})
