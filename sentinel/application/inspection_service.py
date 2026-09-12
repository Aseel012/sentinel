"""Bounded application access to remembered incidents and derived inspection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sentinel.analysis.diagnosis import Diagnosis
from sentinel.application.diagnosis_service import DiagnosisService
from sentinel.models import IncidentCandidate, IncidentState
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import DEFAULT_INCIDENT_LIMIT, IncidentRepository
from sentinel.storage.migrations import initialize_schema


@dataclass(frozen=True, slots=True)
class IncidentInspection:
    incident: IncidentCandidate
    diagnosis: Diagnosis

    def __post_init__(self) -> None:
        if not isinstance(self.incident, IncidentCandidate):
            raise TypeError("inspection incident must be an IncidentCandidate")
        if not isinstance(self.diagnosis, Diagnosis):
            raise TypeError("inspection diagnosis must be a Diagnosis")
        if self.incident.incident_id != self.diagnosis.incident_id:
            raise ValueError("inspection incident and diagnosis identities must match")
        if self.incident.subject_id != self.diagnosis.subject_id:
            raise ValueError("inspection incident and diagnosis subjects must match")


class IncidentInspectionService:
    """Read incidents without recomputing lifecycle or duplicating diagnosis rules."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = database_path

    def list_incidents(
        self,
        *,
        limit: int = DEFAULT_INCIDENT_LIMIT,
        state: IncidentState | None = None,
    ) -> tuple[IncidentCandidate, ...]:
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            return IncidentRepository(connection).list_recent(limit=limit, state=state)

    def inspect(self, incident_id: str) -> IncidentInspection | None:
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            incident = repository.get(incident_id)
            if incident is None:
                return None
            diagnosis = DiagnosisService.derive(repository, incident)
            return IncidentInspection(incident, diagnosis)
