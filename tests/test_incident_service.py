from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from sentinel.analysis import ServiceLifecycle, TimedServiceChange
from sentinel.analysis.services import ServiceChange
from sentinel.application.incident_service import IncidentFormationService
from sentinel.models import CollectionStatus, EventObservation, IncidentState, ServiceObservation
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import IncidentRepository

NOW = datetime(2026, 2, 1, tzinfo=UTC)


def change(previous: str, current: str) -> TimedServiceChange:
    before = ServiceObservation("api.service", "loaded", previous, previous)
    after = ServiceObservation("api.service", "loaded", current, current)
    return TimedServiceChange(ServiceChange(ServiceLifecycle.STATE_CHANGED, "api.service", before, after), NOW)


def event() -> EventObservation:
    return EventObservation("cursor-1", NOW, "systemd", 3, "api.service", None, None, "not persisted", "boot")


class IncidentFormationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sentinel.db"
        self.service = IncidentFormationService(self.path)

    def record(self, service_changes, *, service_status=CollectionStatus.SUCCESS, events=()):
        return self.service.record(
            service_changes, (), events, observed_at=NOW,
            service_status=service_status, process_status=CollectionStatus.SUCCESS,
            journal_status=CollectionStatus.SUCCESS,
        )

    def test_formation_is_persisted_and_repeated_input_is_idempotent(self) -> None:
        opened = self.record((change("active", "failed"),), events=(event(),))
        repeated = self.record((change("active", "failed"),), events=(event(),))
        self.assertEqual(opened.candidates, repeated.candidates)
        with database_connection(self.path) as connection:
            incidents = IncidentRepository(connection).list_recent()
            self.assertEqual(len(incidents), 1)
            self.assertNotIn("not persisted", " ".join(item.summary or ""
                                                       for item in incidents[0].evidence))

    def test_complete_recovery_resolves_but_partial_recovery_does_not(self) -> None:
        opened = self.record((change("active", "failed"),), events=(event(),)).candidates[0]
        self.record((change("failed", "active"),), service_status=CollectionStatus.PARTIAL)
        with database_connection(self.path) as connection:
            self.assertEqual(IncidentRepository(connection).get(opened.incident_id).state,
                             IncidentState.ACTIVE)
        self.record((change("failed", "active"),))
        with database_connection(self.path) as connection:
            self.assertEqual(IncidentRepository(connection).get(opened.incident_id).state,
                             IncidentState.RESOLVED)

    def test_one_shot_service_change_iterable_still_applies_resolution(self) -> None:
        opened = self.record((change("active", "failed"),), events=(event(),)).candidates[0]
        self.record((item for item in (change("failed", "active"),)))
        with database_connection(self.path) as connection:
            self.assertEqual(IncidentRepository(connection).get(opened.incident_id).state,
                             IncidentState.RESOLVED)

    def test_failed_service_collection_cannot_supply_temporal_facts(self) -> None:
        with self.assertRaises(ValueError):
            self.record((change("active", "failed"),),
                        service_status=CollectionStatus.TRANSIENT_FAILURE, events=(event(),))
