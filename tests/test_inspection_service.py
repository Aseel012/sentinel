from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from sentinel.analysis import ProcessLifecycle
from sentinel.application.inspection_service import IncidentInspectionService
from sentinel.models import IncidentState, SubjectType
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import IncidentRepository
from sentinel.storage.migrations import initialize_schema
from tests.test_diagnosis import candidate


NOW = datetime(2026, 4, 1, tzinfo=UTC)


class IncidentInspectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "sentinel.db"

    def store(self, *items) -> None:
        with database_connection(self.path) as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            for item in items:
                repository.reconcile(
                    (item,), resolved_subjects=(), observed_at=item.last_observed_at,
                    resolution_authoritative=False,
                )

    def test_empty_list_and_bounds_are_explicit(self) -> None:
        service = IncidentInspectionService(self.path)
        self.assertEqual(service.list_incidents(), ())
        with self.assertRaises(ValueError):
            service.list_incidents(limit=0)

    def test_multiple_incidents_are_bounded_and_deterministically_newest_first(self) -> None:
        older = candidate(at=NOW)
        newer = candidate(at=NOW + timedelta(minutes=1))
        self.store(older, newer)
        service = IncidentInspectionService(self.path)
        self.assertEqual(service.list_incidents(limit=1), (newer,))
        self.assertEqual(service.list_incidents(), (newer, older))
        self.assertEqual(service.list_incidents(), service.list_incidents())
        self.assertEqual(service.list_incidents(state=IncidentState.RESOLVED), ())

    def test_inspection_reuses_diagnosis_and_preserves_active_lifecycle(self) -> None:
        item = candidate(process_lifecycle=ProcessLifecycle.EXITED, at=NOW)
        self.store(item)
        result = IncidentInspectionService(self.path).inspect(item.incident_id)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.incident, item)
        self.assertEqual(result.incident.state, IncidentState.ACTIVE)
        self.assertEqual(result.diagnosis.incident_id, item.incident_id)
        self.assertIn("exact root cause", result.diagnosis.explanation)

    def test_resolved_state_is_read_not_recomputed(self) -> None:
        item = candidate(at=NOW)
        resolved_at = NOW + timedelta(minutes=1)
        self.store(item)
        with database_connection(self.path) as connection:
            repository = IncidentRepository(connection)
            repository.reconcile(
                (), resolved_subjects=((SubjectType.SERVICE, item.subject_id),),
                observed_at=resolved_at, resolution_authoritative=True,
            )
        result = IncidentInspectionService(self.path).inspect(item.incident_id)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.incident.state, IncidentState.RESOLVED)
        self.assertEqual(result.incident.resolved_at, resolved_at)

    def test_missing_and_storage_failure_are_not_fabricated_as_success(self) -> None:
        service = IncidentInspectionService(self.path)
        self.assertIsNone(service.inspect("f" * 64))
        with self.assertRaises(OSError):
            IncidentInspectionService(self.directory).list_incidents()
