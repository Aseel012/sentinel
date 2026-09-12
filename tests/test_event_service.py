"""Application regressions for failed polls and competing checkpoint writers."""

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sentinel.application.event_service import JournalEventService
from sentinel.models import CollectionResult, CollectionStatus, EventObservation, JournalBatch
from sentinel.storage.database import database_connection
from sentinel.storage.events import journal_cursor, latest_journal_quality, load_events, store_journal_batch


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def batch(cursor: str) -> JournalBatch:
    return JournalBatch((EventObservation(cursor, NOW, "journal", 5, None, None, None, "started", "boot"),), cursor)


class EventServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "events.db"

    def test_failure_is_remembered_without_advancing_checkpoint(self) -> None:
        JournalEventService(self.path, lambda _: CollectionResult(batch("first"), CollectionStatus.SUCCESS, NOW, 0)).record()
        failed = CollectionResult(None, CollectionStatus.PERMISSION_DENIED, NOW, 0, "denied", "access denied")
        self.assertEqual(JournalEventService(self.path, lambda _: failed).record(), failed)
        with database_connection(self.path) as connection:
            self.assertEqual(journal_cursor(connection), "first")
            quality = latest_journal_quality(connection)
            self.assertEqual(quality.status, CollectionStatus.PERMISSION_DENIED)
            self.assertEqual(quality.error_code, "denied")
            self.assertEqual(len(load_events(connection)), 1)

    def test_competing_writer_cannot_regress_the_checkpoint_or_insert_stale_events(self) -> None:
        def collect(_):
            with database_connection(self.path) as connection:
                store_journal_batch(connection, batch("winner"))
            return CollectionResult(batch("stale"), CollectionStatus.SUCCESS, NOW, 0)

        result = JournalEventService(self.path, collect).record()
        self.assertEqual(result.status, CollectionStatus.TRANSIENT_FAILURE)
        self.assertEqual(result.error_code, "journal_checkpoint_conflict")
        with database_connection(self.path) as connection:
            self.assertEqual(journal_cursor(connection), "winner")
            self.assertEqual([event.cursor for event in load_events(connection)], ["winner"])

    def test_empty_poll_keeps_checkpoint_and_records_successful_empty_quality(self) -> None:
        JournalEventService(self.path, lambda _: CollectionResult(batch("first"), CollectionStatus.SUCCESS, NOW, 0)).record()
        result = JournalEventService(self.path, lambda cursor: CollectionResult(
            JournalBatch((), cursor), CollectionStatus.SUCCESS, NOW, 0)).record()
        self.assertEqual(result.value.events, ())
        with database_connection(self.path) as connection:
            self.assertEqual(journal_cursor(connection), "first")
            self.assertEqual(latest_journal_quality(connection).value, 0)

    def test_database_failure_remains_visible(self) -> None:
        def collect(_):
            raise sqlite3.OperationalError("disk I/O error")

        with self.assertRaises(sqlite3.OperationalError):
            JournalEventService(self.path, collect).record()

    def test_event_limit_is_validated_before_collection(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                JournalEventService(self.path, max_events=value)

    def test_oversized_injected_batch_is_rejected_without_advancing_cursor(self) -> None:
        events = tuple(EventObservation(f"cursor-{index}", NOW, "journal", 5, None,
                                        None, None, "bounded", "boot")
                       for index in range(1_001))
        oversized = CollectionResult(JournalBatch(events, events[-1].cursor),
                                     CollectionStatus.SUCCESS, NOW, 0)
        with self.assertRaises(ValueError):
            JournalEventService(self.path, lambda _: oversized).record()
        with database_connection(self.path) as connection:
            self.assertIsNone(journal_cursor(connection))
            self.assertEqual(load_events(connection), ())
