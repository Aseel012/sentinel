from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from sentinel.application.event_query_service import EventQueryService
from sentinel.models import EventObservation, JournalBatch
from sentinel.storage.database import database_connection
from sentinel.storage.events import store_journal_batch
from sentinel.storage.migrations import initialize_schema
from tests.test_diagnosis import service_failure


NOW = datetime(2026, 9, 1, tzinfo=UTC)


class EventQueryServiceTests(unittest.TestCase):
    def test_exact_unit_window_is_deterministic_bounded_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            events = (
                EventObservation("cursor-1", NOW - timedelta(seconds=1), "journal", 3,
                                 "api.service", None, None, "private one", "boot"),
                EventObservation("cursor-2", NOW, "journal", 3,
                                 "api.service", None, None, "private two", "boot"),
                EventObservation("cursor-3", NOW, "journal", 3,
                                 "other.service", None, None, "private other", "boot"),
                EventObservation("cursor-4", NOW + timedelta(seconds=1), "journal", 3,
                                 "api.service", None, None, "private four", "boot"),
            )
            with database_connection(path) as connection:
                initialize_schema(connection)
                store_journal_batch(connection, JournalBatch(events, events[-1].cursor))
            changes = (service_failure(NOW),)
            service = EventQueryService(path)
            first = service.load_for_service_changes(
                changes, correlation_window=timedelta(seconds=2), limit=2,
            )
            second = service.load_for_service_changes(
                changes, correlation_window=timedelta(seconds=2), limit=2,
            )
            self.assertEqual(first, second)
            self.assertEqual(tuple(item.cursor for item in first.events),
                             ("cursor-1", "cursor-2"))
            self.assertTrue(first.truncated)
            self.assertNotIn("cursor-3", {item.cursor for item in first.events})

    def test_non_anchor_change_does_not_load_unrelated_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            service = EventQueryService(path)
            failed = service_failure(NOW)
            non_anchor = type(failed)(
                type(failed.change)(failed.change.lifecycle, failed.change.name,
                                    failed.change.previous, failed.change.previous),
                failed.observed_at,
            )
            result = service.load_for_service_changes(
                (non_anchor,), correlation_window=timedelta(seconds=2), limit=2,
            )
            self.assertEqual(result.events, ())
            self.assertFalse(result.truncated)
