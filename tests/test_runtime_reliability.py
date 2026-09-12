from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sentinel.application.event_service import JournalEventService
from sentinel.application.incident_service import IncidentFormationService
from sentinel.application.persistence_service import PersistentSnapshotService
from sentinel.application.runtime import ContinuousObservationRuntime
from sentinel.application.runtime_models import RuntimeConfig
from sentinel.models import CollectionResult, CollectionStatus, EventObservation, JournalBatch
from sentinel.storage.database import database_connection
from sentinel.storage.events import journal_cursor
from tests.test_runtime import IncrementingClock, NOW, collection_at


class SnapshotSource:
    def __init__(self, *values) -> None:
        self.values = list(values)

    def collect(self):
        return self.values.pop(0)


class FailedEventQuery:
    def load_for_service_changes(self, changes, *, correlation_window, limit):
        raise sqlite3.OperationalError("forced event query failure")


def journal_collector(event: EventObservation):
    def collect(cursor):
        batch = (
            JournalBatch((event,), event.cursor)
            if cursor is None
            else JournalBatch((), cursor)
        )
        return CollectionResult(
            batch,
            CollectionStatus.SUCCESS,
            event.timestamp,
            0,
        )

    return collect


class RuntimeFailureIntegrationTests(unittest.TestCase):
    def test_event_query_failure_preserves_committed_snapshot_event_and_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            event = EventObservation(
                "query-failure-cursor",
                NOW,
                "journal",
                3,
                "worker.service",
                None,
                None,
                "private body",
                "boot",
            )
            runtime = ContinuousObservationRuntime(
                path,
                RuntimeConfig(interval_seconds=1),
                snapshot_recorder=PersistentSnapshotService(
                    path,
                    SnapshotSource(collection_at(NOW)),
                ),
                event_recorder=JournalEventService(path, journal_collector(event)),
                event_reader=FailedEventQuery(),
                monotonic=IncrementingClock(),
            )
            with self.assertRaisesRegex(sqlite3.OperationalError, "event query"):
                runtime.record()
            self.assertIsNone(runtime._previous_collection)
            with database_connection(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    1,
                )
                self.assertEqual(journal_cursor(connection), event.cursor)

    def test_incident_transaction_failure_rolls_back_only_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            event = EventObservation(
                "incident-failure-cursor",
                NOW + timedelta(seconds=5),
                "journal",
                3,
                "worker.service",
                None,
                None,
                "private body",
                "boot",
            )
            runtime = ContinuousObservationRuntime(
                path,
                RuntimeConfig(interval_seconds=1),
                snapshot_recorder=PersistentSnapshotService(
                    path,
                    SnapshotSource(
                        collection_at(NOW, "active"),
                        collection_at(NOW + timedelta(seconds=10), "failed"),
                    ),
                ),
                event_recorder=JournalEventService(path, journal_collector(event)),
                incident_recorder=IncidentFormationService(path),
                monotonic=IncrementingClock(),
            )
            runtime.record()
            with database_connection(path) as connection:
                connection.execute(
                    """CREATE TRIGGER force_incident_failure
                       BEFORE INSERT ON incidents
                       BEGIN SELECT RAISE(ABORT, 'forced incident failure'); END"""
                )
                connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "forced incident"):
                runtime.record()
            self.assertEqual(runtime._cycles_completed, 1)
            service = runtime._previous_collection.snapshot.services[0]
            self.assertEqual(service.active_state, "active")
            with database_connection(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
                    0,
                )
                self.assertEqual(journal_cursor(connection), event.cursor)
