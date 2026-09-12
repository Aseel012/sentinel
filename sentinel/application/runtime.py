"""Bounded foreground orchestration of existing Sentinel application services."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from sentinel.analysis import (
    TimedProcessChange,
    TimedServiceChange,
    compare_process_collections,
    compare_service_collections,
)
from sentinel.application.event_query_service import (
    CorrelationEvents,
    EventQueryService,
)
from sentinel.application.event_service import JournalEventService
from sentinel.application.incident_service import IncidentFormation, IncidentFormationService
from sentinel.application.persistence_service import PersistentSnapshotService
from sentinel.application.runtime_models import (
    MAX_CYCLE_LIMITATIONS,
    MAX_RUNTIME_CYCLES,
    RuntimeConfig,
    RuntimeCycle,
)
from sentinel.application.sampler import Sampler, SamplingConfig, SamplingRun
from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import CollectionResult, CollectionStatus, JournalBatch
from sentinel.storage.snapshots import StoredSnapshot


class SnapshotRecorder(Protocol):
    def record(self) -> StoredSnapshot: ...


class EventRecorder(Protocol):
    def record(self) -> CollectionResult[JournalBatch]: ...


class IncidentRecorder(Protocol):
    def record(self, *args, **kwargs) -> IncidentFormation: ...


class EventReader(Protocol):
    def load_for_service_changes(
        self,
        changes: tuple[TimedServiceChange, ...],
        *,
        correlation_window: timedelta,
        limit: int,
    ) -> CorrelationEvents: ...


class ContinuousObservationRuntime:
    """Compose one snapshot, journal, temporal-analysis, and incident cycle at a time."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        config: RuntimeConfig | None = None,
        *,
        snapshot_recorder: SnapshotRecorder | None = None,
        event_recorder: EventRecorder | None = None,
        event_reader: EventReader | None = None,
        incident_recorder: IncidentRecorder | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
        cycle_sink: Callable[[RuntimeCycle], None] | None = None,
    ) -> None:
        if config is not None and not isinstance(config, RuntimeConfig):
            raise TypeError("config must be a RuntimeConfig or None")
        self._config = RuntimeConfig() if config is None else config
        self._snapshot_recorder = (
            PersistentSnapshotService(
                database_path,
                max_snapshots=self._config.max_snapshots,
            )
            if snapshot_recorder is None
            else snapshot_recorder
        )
        self._event_recorder = (
            JournalEventService(database_path)
            if event_recorder is None
            else event_recorder
        )
        self._event_reader = (
            EventQueryService(database_path)
            if event_reader is None
            else event_reader
        )
        self._incident_recorder = (
            IncidentFormationService(database_path)
            if incident_recorder is None
            else incident_recorder
        )
        self._monotonic = monotonic
        self._cycle_sink = cycle_sink
        self._previous_collection: SnapshotCollection | None = None
        self._previous_observed_monotonic: float | None = None
        self._cycles_completed = 0
        self._sampler = Sampler(
            self,
            SamplingConfig(self._config.interval_seconds),
            monotonic=monotonic,
            wait=wait,
        )

    def request_stop(self) -> None:
        self._sampler.request_stop()

    def run(self, *, max_cycles: int | None = None) -> SamplingRun:
        if max_cycles is not None and (
            isinstance(max_cycles, bool) or not isinstance(max_cycles, int)
            or not 1 <= max_cycles <= MAX_RUNTIME_CYCLES
        ):
            raise ValueError(f"max_cycles must be between 1 and {MAX_RUNTIME_CYCLES}, or None")
        return self._sampler.run(max_samples=max_cycles)

    def record(self) -> RuntimeCycle:
        """Execute one cycle; storage and invariant failures intentionally propagate."""
        started = _monotonic_value(self._monotonic(), "cycle start")
        stored = self._snapshot_recorder.record()
        current = stored.collection
        observed_monotonic = _monotonic_value(self._monotonic(), "snapshot observation")
        elapsed = (None if self._previous_observed_monotonic is None
                   else observed_monotonic - self._previous_observed_monotonic)
        journal = self._event_recorder.record()
        service_status = _collection_status(current, "services")
        process_status = _collection_status(current, "processes")
        services = compare_service_collections(self._previous_collection, current)
        processes = compare_process_collections(self._previous_collection, current, elapsed)
        observed_at = current.snapshot.timestamp
        service_observed_at = _collection_timestamp(current, "services")
        process_observed_at = _collection_timestamp(current, "processes")
        service_changes = tuple(TimedServiceChange(item, service_observed_at)
                                for item in services.changes)
        process_changes = tuple(TimedProcessChange(item, process_observed_at)
                                for item in processes.changes)
        accepted_events = () if journal.value is None else journal.value.events
        correlation_events = self._event_reader.load_for_service_changes(
            service_changes,
            correlation_window=self._config.correlation_window,
            limit=self._config.event_query_limit,
        )
        formation = self._incident_recorder.record(
            service_changes,
            process_changes,
            correlation_events.events,
            observed_at=service_observed_at,
            service_status=service_status,
            process_status=process_status,
            journal_status=journal.status,
            associations=(),
            correlation_window=self._config.correlation_window,
            max_evidence=self._config.max_evidence,
            max_inputs=self._config.max_inputs,
        )
        completed = _monotonic_value(self._monotonic(), "cycle completion")
        if completed < started:
            raise ValueError("monotonic clock moved backwards during a runtime cycle")
        self._cycles_completed += 1
        result = RuntimeCycle(
            self._cycles_completed,
            observed_at,
            started,
            completed,
            completed - started,
            stored.id,
            tuple((name, value.status) for name, value in current.collector_results),
            _observation_counts(current),
            journal.status,
            len(accepted_events),
            len(correlation_events.events),
            tuple(item.incident_id for item in formation.candidates),
            formation.reconciliation.candidate_ids,
            formation.reconciliation.resolved_ids,
            _cycle_limitations(current, journal, correlation_events),
        )
        self._previous_collection = current
        self._previous_observed_monotonic = observed_monotonic
        if self._cycle_sink is not None:
            self._cycle_sink(result)
        return result


def _collection_status(collection: SnapshotCollection, name: str) -> CollectionStatus:
    matches = tuple(result.status for key, result in collection.collector_results if key == name)
    if len(matches) != 1:
        raise ValueError(f"runtime requires exactly one {name} collection result")
    return matches[0]


def _collection_timestamp(collection: SnapshotCollection, name: str) -> datetime:
    matches = tuple(
        result.collected_at
        for key, result in collection.collector_results
        if key == name
    )
    if len(matches) != 1:
        raise ValueError(f"runtime requires exactly one {name} collection result")
    return matches[0]


def _observation_counts(collection: SnapshotCollection) -> tuple[tuple[str, int], ...]:
    snapshot = collection.snapshot
    return (
        ("cpu", int(snapshot.cpu is not None)),
        ("disk", len(snapshot.disk)),
        ("memory", int(snapshot.memory is not None)),
        ("network", len(snapshot.network)),
        ("processes", len(snapshot.processes)),
        ("services", len(snapshot.services)),
        ("system", int(snapshot.system is not None)),
    )


def _cycle_limitations(
    collection: SnapshotCollection,
    journal: CollectionResult[JournalBatch],
    correlation_events: CorrelationEvents,
) -> tuple[str, ...]:
    values = {
        f"collector_{name}_{result.status.value}"
        for name, result in collection.collector_results
        if result.status is not CollectionStatus.SUCCESS
    }
    if journal.status is not CollectionStatus.SUCCESS:
        values.add(f"journal_collection_{journal.status.value}")
    if any("limit reached" in warning for warning in journal.warnings):
        values.add("journal_event_limit_reached")
    if correlation_events.truncated:
        values.add("correlation_event_window_truncated")
    ordered = sorted(values)
    if len(ordered) <= MAX_CYCLE_LIMITATIONS:
        return tuple(ordered)
    return tuple(sorted((*ordered[:MAX_CYCLE_LIMITATIONS - 1], "runtime_limitations_truncated")))


def _monotonic_value(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} monotonic value must be finite")
    return float(value)
