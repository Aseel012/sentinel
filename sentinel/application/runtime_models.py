"""Immutable bounded contracts for continuous Sentinel runtime cycles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math

from sentinel.analysis import (
    DEFAULT_CORRELATION_WINDOW,
    DEFAULT_MAX_EVIDENCE,
    DEFAULT_MAX_INPUTS,
)
from sentinel.analysis.correlation import MAX_INPUTS
from sentinel.application.event_query_service import DEFAULT_RUNTIME_EVENT_LIMIT
from sentinel.models import CollectionStatus
from sentinel.models.incidents import MAX_EVIDENCE_PER_INCIDENT

MIN_RUNTIME_INTERVAL_SECONDS = 0.1
MAX_RUNTIME_INTERVAL_SECONDS = 86_400.0
MAX_RUNTIME_CYCLES = 100_000
MAX_CYCLE_LIMITATIONS = 32
DEFAULT_RUNTIME_SNAPSHOT_LIMIT = 10_000
MAX_RUNTIME_SNAPSHOT_LIMIT = 100_000


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    interval_seconds: float = 60.0
    correlation_window: timedelta = DEFAULT_CORRELATION_WINDOW
    max_evidence: int = DEFAULT_MAX_EVIDENCE
    max_inputs: int = DEFAULT_MAX_INPUTS
    event_query_limit: int = DEFAULT_RUNTIME_EVENT_LIMIT
    max_snapshots: int = DEFAULT_RUNTIME_SNAPSHOT_LIMIT

    def __post_init__(self) -> None:
        value = self.interval_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("interval_seconds must be a real number")
        if (not math.isfinite(value)
                or not MIN_RUNTIME_INTERVAL_SECONDS <= value <= MAX_RUNTIME_INTERVAL_SECONDS):
            raise ValueError(
                "interval_seconds must be finite and between "
                f"{MIN_RUNTIME_INTERVAL_SECONDS} and {MAX_RUNTIME_INTERVAL_SECONDS}"
            )
        if not isinstance(self.correlation_window, timedelta):
            raise TypeError("correlation_window must be a timedelta")
        window_seconds = self.correlation_window.total_seconds()
        if not math.isfinite(window_seconds) or window_seconds < 0:
            raise ValueError("correlation_window must be finite and non-negative")
        if (isinstance(self.max_evidence, bool) or not isinstance(self.max_evidence, int)
                or not 2 <= self.max_evidence <= MAX_EVIDENCE_PER_INCIDENT):
            raise ValueError("max_evidence is outside the incident evidence bound")
        if (isinstance(self.max_inputs, bool) or not isinstance(self.max_inputs, int)
                or not 1 <= self.max_inputs <= MAX_INPUTS):
            raise ValueError(f"max_inputs must be between 1 and {MAX_INPUTS}")
        if (isinstance(self.event_query_limit, bool)
                or not isinstance(self.event_query_limit, int)
                or not 1 <= self.event_query_limit <= 10_000):
            raise ValueError("event_query_limit must be between 1 and 10000")
        if (isinstance(self.max_snapshots, bool) or not isinstance(self.max_snapshots, int)
                or not 1 <= self.max_snapshots <= MAX_RUNTIME_SNAPSHOT_LIMIT):
            raise ValueError(
                f"max_snapshots must be between 1 and {MAX_RUNTIME_SNAPSHOT_LIMIT}"
            )


@dataclass(frozen=True, slots=True)
class RuntimeCycle:
    cycle_number: int
    observed_at: datetime
    started_monotonic: float
    completed_monotonic: float
    duration_seconds: float
    snapshot_id: int
    collection_quality: tuple[tuple[str, CollectionStatus], ...]
    observation_counts: tuple[tuple[str, int], ...]
    journal_status: CollectionStatus
    events_processed: int
    events_correlated: int
    incident_candidate_ids: tuple[str, ...]
    reconciled_candidate_ids: tuple[str, ...]
    resolved_incident_ids: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if (isinstance(self.cycle_number, bool) or not isinstance(self.cycle_number, int)
                or self.cycle_number < 1):
            raise ValueError("cycle_number must be positive")
        if (not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None
                or self.observed_at.utcoffset() != UTC.utcoffset(self.observed_at)):
            raise ValueError("observed_at must be timezone-aware UTC")
        values = (self.started_monotonic, self.completed_monotonic, self.duration_seconds)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in values):
            raise ValueError("runtime timing values must be finite numbers")
        if self.completed_monotonic < self.started_monotonic or self.duration_seconds < 0:
            raise ValueError("runtime timing cannot move backwards")
        if self.duration_seconds != self.completed_monotonic - self.started_monotonic:
            raise ValueError("duration_seconds must match monotonic cycle bounds")
        if (not isinstance(self.snapshot_id, int) or isinstance(self.snapshot_id, bool)
                or self.snapshot_id < 1):
            raise ValueError("snapshot_id must be positive")
        if not isinstance(self.journal_status, CollectionStatus):
            raise TypeError("journal_status must be a CollectionStatus")
        counts = tuple(value for _, value in self.observation_counts) + (
            self.events_processed, self.events_correlated,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in counts):
            raise ValueError("runtime counts must be non-negative integers")
        names = tuple(name for name, _ in self.collection_quality)
        if len(names) != len(set(names)):
            raise ValueError("runtime collection quality names must be unique")
        if any(not isinstance(status, CollectionStatus) for _, status in self.collection_quality):
            raise TypeError("runtime collection quality must use CollectionStatus")
        count_names = tuple(name for name, _ in self.observation_counts)
        if count_names != tuple(sorted(set(count_names))):
            raise ValueError("runtime observation count names must be unique and sorted")
        for identities in (self.incident_candidate_ids, self.reconciled_candidate_ids,
                           self.resolved_incident_ids):
            if (not isinstance(identities, tuple) or len(identities) > MAX_INPUTS
                    or len(identities) != len(set(identities))
                    or any(len(value) != 64
                           or any(character not in "0123456789abcdef" for character in value)
                           for value in identities)):
                raise ValueError("runtime incident identities must be bounded and unique")
        if (len(self.limitations) > MAX_CYCLE_LIMITATIONS
                or self.limitations != tuple(sorted(set(self.limitations)))):
            raise ValueError("runtime limitations must be unique, sorted, and bounded")
        if any(not value or len(value) > 256 for value in self.limitations):
            raise ValueError("runtime limitation values must be bounded and nonempty")
