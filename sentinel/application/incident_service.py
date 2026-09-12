"""Application boundary for deterministic incident formation and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Iterable, TypeVar

from sentinel.analysis.correlation import (
    DEFAULT_CORRELATION_WINDOW,
    DEFAULT_MAX_EVIDENCE,
    DEFAULT_MAX_INPUTS,
    MAX_INPUTS,
    ServiceProcessAssociation,
    TimedProcessChange,
    TimedServiceChange,
    correlate_service_incidents,
    resolution_subjects,
)
from sentinel.models import CollectionStatus, EventObservation, IncidentCandidate, SubjectType
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import IncidentReconciliation, IncidentRepository
from sentinel.storage.migrations import initialize_schema

T = TypeVar("T", TimedServiceChange, TimedProcessChange)


@dataclass(frozen=True, slots=True)
class IncidentFormation:
    """Candidates derived during this call and their durable lifecycle changes."""

    candidates: tuple[IncidentCandidate, ...]
    reconciliation: IncidentReconciliation


class IncidentFormationService:
    """Correlate supplied temporal facts, then persist their derived incident state."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = database_path

    def record(
        self,
        service_changes: Iterable[TimedServiceChange],
        process_changes: Iterable[TimedProcessChange],
        journal_events: Iterable[EventObservation],
        *,
        observed_at: datetime,
        service_status: CollectionStatus,
        process_status: CollectionStatus,
        journal_status: CollectionStatus,
        associations: Iterable[ServiceProcessAssociation] = (),
        correlation_window: timedelta = DEFAULT_CORRELATION_WINDOW,
        max_evidence: int = DEFAULT_MAX_EVIDENCE,
        max_inputs: int = DEFAULT_MAX_INPUTS,
    ) -> IncidentFormation:
        """Record one bounded correlation pass without inferring facts from failures."""
        for label, status in (("service", service_status), ("process", process_status),
                              ("journal", journal_status)):
            if not isinstance(status, CollectionStatus):
                raise TypeError(f"{label}_status must be a CollectionStatus")

        if (isinstance(max_inputs, bool) or not isinstance(max_inputs, int)
                or not 1 <= max_inputs <= MAX_INPUTS):
            raise ValueError(f"max_inputs must be between 1 and {MAX_INPUTS}")
        service_values = _bounded_values(service_changes, "service changes", max_inputs)
        process_values = _bounded_values(process_changes, "process changes", max_inputs)
        qualified_services = tuple(_quality_aware_changes(service_values, "service", service_status))
        qualified_processes = tuple(_quality_aware_changes(process_values, "process", process_status))
        candidates = correlate_service_incidents(
            qualified_services,
            qualified_processes,
            journal_events,
            associations=associations,
            journal_status=journal_status,
            correlation_window=correlation_window,
            max_evidence=max_evidence,
            max_inputs=max_inputs,
        )
        resolutions = tuple(
            (SubjectType.SERVICE, name)
            for name in resolution_subjects(qualified_services)
        )
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            reconciliation = IncidentRepository(connection).reconcile(
                candidates,
                resolved_subjects=resolutions,
                observed_at=observed_at,
                resolution_authoritative=service_status is CollectionStatus.SUCCESS,
            )
        return IncidentFormation(candidates, reconciliation)


def _quality_aware_changes(
    changes: Iterable[T],
    source: str,
    status: CollectionStatus,
) -> Iterable[T]:
    available = status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL)
    limitation = None if status is CollectionStatus.SUCCESS else f"{source}_collection_{status.value}"
    for item in changes:
        if not available:
            raise ValueError(f"{source} changes cannot accompany an unavailable collection")
        if limitation is None or limitation in item.quality_limitations:
            yield item
            continue
        limitations = tuple(sorted((*item.quality_limitations, limitation)))
        yield replace(item, quality_limitations=limitations)


def _bounded_values(values: Iterable[T], label: str, limit: int) -> tuple[T, ...]:
    materialized = tuple(islice(values, limit + 1))
    if len(materialized) > limit:
        raise ValueError(f"{label} exceed max_inputs")
    return materialized
