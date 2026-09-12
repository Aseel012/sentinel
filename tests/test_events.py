from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from sentinel.collectors.journal import JournalBatch
from sentinel.application.event_service import JournalEventService
from sentinel.models import CollectionResult, CollectionStatus, EventObservation
from sentinel.storage.database import database_connection
from sentinel.storage.events import (JournalCheckpointConflict, delete_events_before, journal_cursor,
                                     latest_journal_quality, load_events, store_journal_batch,
                                     store_journal_result)
from sentinel.storage.migrations import initialize_schema
from sentinel.storage.retention import delete_snapshots_before


def event(cursor: str, timestamp: datetime) -> EventObservation:
    return EventObservation(cursor, timestamp, "journal", 5, "api.service", 12, "api", "message", "boot")


class EventStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._path = Path(self._directory.name) / "sentinel.db"

    def test_roundtrip_quality_retention_and_subsecond_order(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        detailed = EventObservation("second", now + timedelta(microseconds=1), "journal", 5,
                                   "api.service", 12, "api", "message", "boot", ("message_truncated",))
        with database_connection(self._path) as connection:
            initialize_schema(connection)
            batch = JournalBatch((event("first", now), detailed), "second")
            result = CollectionResult(batch, CollectionStatus.PARTIAL, now, 0.2, warnings=("bounded",))
            store_journal_result(connection, result, expected_cursor=None, max_events=1)
            self.assertEqual(load_events(connection, limit=1, unit="api.service"), (detailed,))
            self.assertEqual(latest_journal_quality(connection),
                             CollectionResult(2, CollectionStatus.PARTIAL, now, 0.2, warnings=("bounded",)))
            self.assertEqual(load_events(connection, before=now + timedelta(microseconds=1)), ())
            self.assertEqual(journal_cursor(connection), "second")
            with self.assertRaises(ValueError):
                load_events(connection, limit=10001)

    def test_failed_collection_preserves_checkpoint_and_stale_writer_rolls_back(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        with database_connection(self._path) as connection:
            initialize_schema(connection)
            store_journal_batch(connection, JournalBatch((event("first", now),), "first"), expected_cursor=None)
            failure = CollectionResult(None, CollectionStatus.PERMISSION_DENIED, now, 0.0, "denied")
            store_journal_result(connection, failure, expected_cursor="first")
            self.assertEqual(latest_journal_quality(connection), failure)
            with self.assertRaises(JournalCheckpointConflict):
                store_journal_batch(connection, JournalBatch((event("stale", now),), "stale"), expected_cursor=None)
            self.assertEqual(journal_cursor(connection), "first")
            self.assertEqual([item.cursor for item in load_events(connection)], ["first"])

    def test_invalid_batch_and_retention_configuration_roll_back(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        with database_connection(self._path) as connection:
            initialize_schema(connection)
            for batch, cap in ((JournalBatch((event("one", now),), "wrong"), 10),
                               (JournalBatch((event("one", now),), "one"), 0),
                               (JournalBatch((), "nonexistent"), 10)):
                with self.assertRaises(ValueError):
                    store_journal_batch(connection, batch, max_events=cap)
                self.assertEqual(load_events(connection), ())
                self.assertIsNone(journal_cursor(connection))

    def test_events_are_idempotent_and_checkpointed_independently(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        batch = JournalBatch((event("cursor-1", now), event("cursor-2", now + timedelta(seconds=1))), "cursor-2")
        with database_connection(self._path) as connection:
            initialize_schema(connection)
            self.assertEqual(store_journal_batch(connection, batch), 2)
            self.assertEqual(store_journal_batch(connection, batch), 0)
            self.assertEqual(journal_cursor(connection), "cursor-2")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

    def test_event_retention_is_explicit_and_validates_utc_cutoffs(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        with database_connection(self._path) as connection:
            initialize_schema(connection)
            store_journal_batch(connection, JournalBatch((event("old", now), event("new", now + timedelta(days=2))), "new"))
            self.assertEqual(delete_events_before(connection, now + timedelta(days=1)), 1)
            self.assertEqual(connection.execute("SELECT cursor FROM events").fetchone()[0], "new")
            with self.assertRaises(ValueError):
                delete_events_before(connection, datetime(2026, 1, 2))

    def test_application_service_resumes_from_durable_checkpoint(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        seen: list[str | None] = []

        def collector(cursor: str | None) -> CollectionResult[JournalBatch]:
            seen.append(cursor)
            batch = JournalBatch((event(f"cursor-{len(seen)}", now),), f"cursor-{len(seen)}")
            return CollectionResult(batch, CollectionStatus.SUCCESS, now, 0.1)

        service = JournalEventService(self._path, collector)
        service.record()
        service.record()
        self.assertEqual(seen, [None, "cursor-1"])

    def test_snapshot_and_event_retention_are_independent_and_keep_checkpoint(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        with database_connection(self._path) as connection:
            initialize_schema(connection)
            store_journal_batch(connection, JournalBatch((event("retained-cursor", now),), "retained-cursor"))
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", (now.isoformat(),))
            connection.commit()
            self.assertEqual(delete_snapshots_before(connection, now + timedelta(days=1)), 1)
            self.assertEqual(len(load_events(connection)), 1)
            self.assertEqual(delete_events_before(connection, now + timedelta(days=1)), 1)
            self.assertEqual(journal_cursor(connection), "retained-cursor")
            self.assertEqual(load_events(connection), ())
