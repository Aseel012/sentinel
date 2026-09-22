"""Application access to derived diagnoses over persisted incident evidence."""

from __future__ import annotations

from pathlib import Path

from sentinel.analysis.diagnosis import Diagnosis, diagnose_incident
from sentinel.models import IncidentCandidate
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import IncidentRepository, MAX_INCIDENT_LIMIT
from sentinel.storage.migrations import initialize_schema

class DiagnosisService:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = database_path

    def diagnose(self, incident_id: str) -> Diagnosis | None:
        """Load one incident and deterministically derive its strongest explanation."""
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            incident = repository.get(incident_id)
            if incident is None:
                return None
            return self.derive(repository, incident)

    @staticmethod
    def derive(repository: IncidentRepository, incident: IncidentCandidate) -> Diagnosis:
        """Derive from an already loaded incident while reusing bounded history policy."""
        if not isinstance(repository, IncidentRepository):
            raise TypeError("repository must be an IncidentRepository")
        if not isinstance(incident, IncidentCandidate):
            raise TypeError("incident must be an IncidentCandidate")
        history = repository.list_recent(
            limit=MAX_INCIDENT_LIMIT,
            subject=(incident.subject_type, incident.subject_id),
        )
        return diagnose_incident(incident, historical_incidents=history)
