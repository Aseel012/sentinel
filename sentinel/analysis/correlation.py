"""Deterministic, evidence-preserving correlation of existing temporal facts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from itertools import islice
import math
from typing import Iterable

from sentinel.models import (
    CollectionStatus,
    EvidenceFact,
    EvidenceKind,
    EvidenceReference,
    EvidenceRelation,
    EventObservation,
    IncidentCandidate,
    IncidentState,
    SubjectType,
)
from sentinel.models.incidents import (
    MAX_EVIDENCE_PER_INCIDENT,
    MAX_LIMITATIONS_PER_ENTITY,
    MAX_LIMITATION_LENGTH,
    MAX_SUBJECT_ID_LENGTH,
    MAX_SUMMARY_LENGTH,
)

from .processes import ProcessChange, ProcessLifecycle
from .services import ServiceChange, ServiceLifecycle


DEFAULT_CORRELATION_WINDOW = timedelta(seconds=30)
DEFAULT_MAX_EVIDENCE = 32
DEFAULT_MAX_INPUTS = 10_000
MAX_INPUTS = 100_000
INCIDENT_RULE_ID = "service-condition-v1"


@dataclass(frozen=True, slots=True)
class TimedServiceChange:
    """A service lifecycle fact paired with its authoritative sample time."""

    change: ServiceChange
    observed_at: datetime
    quality_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.change, ServiceChange):
            raise TypeError("change must be a ServiceChange")
        _require_utc(self.observed_at, "service change timestamp")
        _validate_limitations(self.quality_limitations)


@dataclass(frozen=True, slots=True)
class TimedProcessChange:
    """A process lifecycle fact paired with its authoritative sample time."""

    change: ProcessChange
    observed_at: datetime
    quality_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.change, ProcessChange):
            raise TypeError("change must be a ProcessChange")
        _require_utc(self.observed_at, "process change timestamp")
        _validate_limitations(self.quality_limitations)


@dataclass(frozen=True, slots=True)
class ServiceProcessAssociation:
    """An explicit relationship supplied by a source that knows systemd ownership."""

    service_name: str
    process_lifetime_id: str

    def __post_init__(self) -> None:
        if not _valid_service_name(self.service_name):
            raise ValueError("association requires a canonical service name")
        parts = self.process_lifetime_id.split(":") if isinstance(self.process_lifetime_id, str) else ()
        if (len(parts) != 2 or any(not part.isascii() or not part.isdecimal() for part in parts)
                or int(parts[0]) <= 0 or int(parts[1]) < 0 or len(self.process_lifetime_id) > 64):
            raise ValueError("association requires a process lifetime identity")


def correlate_service_incidents(
    service_changes: Iterable[TimedServiceChange],
    process_changes: Iterable[TimedProcessChange],
    journal_events: Iterable[EventObservation],
    *,
    associations: Iterable[ServiceProcessAssociation] = (),
    journal_status: CollectionStatus = CollectionStatus.SUCCESS,
    correlation_window: timedelta = DEFAULT_CORRELATION_WINDOW,
    max_evidence: int = DEFAULT_MAX_EVIDENCE,
    max_inputs: int = DEFAULT_MAX_INPUTS,
) -> tuple[IncidentCandidate, ...]:
    """Form active incident candidates from explicit identity and time relationships.

    A candidate requires a qualifying service condition plus at least one different
    kind of evidence. Temporal proximity alone never establishes a relationship.
    """
    window = _validate_options(
        correlation_window, max_evidence, max_inputs, journal_status,
    )
    services = _deduplicate_service_changes(
        _bounded_inputs(service_changes, "service changes", max_inputs)
    )
    processes = _deduplicate_process_changes(
        _bounded_inputs(process_changes, "process changes", max_inputs)
    )
    events = _deduplicate_events(_bounded_inputs(journal_events, "journal events", max_inputs))
    associations_by_service = _association_index(
        _bounded_inputs(associations, "service-process associations", max_inputs)
    )
    processes_by_identity = _process_index(processes)
    events_by_unit = _event_index(events)

    candidates: list[IncidentCandidate] = []
    for timed_change in services:
        if not _incident_anchor(timed_change.change):
            continue
        service_name = timed_change.change.name
        anchor = _service_evidence(timed_change)
        related: list[EvidenceReference] = []

        for lifetime_id in associations_by_service.get(service_name, ()):
            related.extend(_nearby_process_evidence(
                processes_by_identity.get(lifetime_id, ()), timed_change.observed_at, window,
                service_name,
            ))
        related.extend(_nearby_event_evidence(
            events_by_unit.get(service_name, ()), timed_change.observed_at, window, service_name,
        ))
        if not related:
            continue

        related.sort(key=lambda evidence: (
            abs((evidence.observed_at - timed_change.observed_at).total_seconds()),
            evidence.observed_at,
            evidence.kind.value,
            evidence.evidence_id,
        ))
        evidence = tuple(sorted(
            (anchor, *related[:max_evidence - 1]), key=_evidence_order,
        ))
        limitations = set(timed_change.quality_limitations)
        limitations.update(item for reference in evidence for item in reference.quality_limitations)
        if journal_status is not CollectionStatus.SUCCESS:
            limitations.add(f"journal_collection_{journal_status.value}")
        candidates.append(IncidentCandidate(
            incident_id=_incident_id(service_name, anchor.evidence_id),
            rule_id=INCIDENT_RULE_ID,
            subject_type=SubjectType.SERVICE,
            subject_id=service_name,
            state=IncidentState.ACTIVE,
            started_at=timed_change.observed_at,
            last_observed_at=max(reference.observed_at for reference in evidence),
            resolved_at=None,
            evidence=evidence,
            quality_limitations=_bounded_limitations(limitations),
        ))
    return tuple(sorted(candidates, key=lambda item: (
        item.started_at, item.subject_id, item.incident_id,
    )))


def is_incident_anchor(change: ServiceChange) -> bool:
    """Expose the authoritative opening rule for bounded evidence lookup."""
    if not isinstance(change, ServiceChange):
        raise TypeError("change must be a ServiceChange")
    return _incident_anchor(change)


def resolution_subjects(
    service_changes: Iterable[TimedServiceChange],
    *,
    max_inputs: int = DEFAULT_MAX_INPUTS,
) -> tuple[str, ...]:
    """Return services with explicit state transitions to active.

    The application layer decides which prior active incident, if any, this resolves.
    A newly observed active service is deliberately not treated as recovery evidence.
    """
    _validate_max_inputs(max_inputs)
    resolved = {
        item.change.name
        for item in _deduplicate_service_changes(
            _bounded_inputs(service_changes, "service changes", max_inputs)
        )
        if item.change.lifecycle is ServiceLifecycle.STATE_CHANGED
        and item.change.current is not None
        and item.change.current.active_state == "active"
        and _consistent_service_change(item.change)
    }
    return tuple(sorted(resolved))


def _incident_anchor(change: ServiceChange) -> bool:
    if not _consistent_service_change(change):
        return False
    if change.lifecycle is ServiceLifecycle.REMOVED:
        return change.previous is not None and change.current is None
    return (change.lifecycle is ServiceLifecycle.STATE_CHANGED
            and change.current is not None
            and change.current.active_state in {"failed", "inactive", "deactivating"})


def _service_evidence(item: TimedServiceChange) -> EvidenceReference:
    change = item.change
    previous = _service_state(change.previous)
    current = _service_state(change.current)
    return EvidenceReference(
        kind=EvidenceKind.SERVICE_CHANGE,
        evidence_id=_service_change_identity(item),
        observed_at=item.observed_at,
        subject_type=SubjectType.SERVICE,
        subject_id=change.name,
        reason=EvidenceRelation.SERVICE_CHANGE_ANCHOR,
        summary=_bounded_summary(f"service {change.lifecycle.value}: {previous} -> {current}"),
        quality_limitations=tuple(sorted(set(item.quality_limitations))),
        fact=_service_fact(change),
    )


def _process_evidence(item: TimedProcessChange, service_name: str) -> EvidenceReference:
    change = item.change
    identity = _digest("process-change-v1", change.lifetime_id or "", item.observed_at.isoformat(),
                       change.lifecycle.value)
    return EvidenceReference(
        kind=EvidenceKind.PROCESS_CHANGE,
        evidence_id=f"process:{identity}",
        observed_at=item.observed_at,
        subject_type=SubjectType.SERVICE,
        subject_id=service_name,
        reason=EvidenceRelation.EXPLICIT_PROCESS_ASSOCIATION,
        summary=_bounded_summary(
            f"associated process lifetime {change.lifetime_id} {change.lifecycle.value}"
        ),
        quality_limitations=tuple(sorted(set(item.quality_limitations))),
        fact={
            ProcessLifecycle.EXITED: EvidenceFact.PROCESS_EXITED,
            ProcessLifecycle.NEW: EvidenceFact.PROCESS_STARTED,
            ProcessLifecycle.IDENTITY_CHANGED: EvidenceFact.PROCESS_IDENTITY_CHANGED,
        }[change.lifecycle],
    )


def _event_evidence(event: EventObservation, service_name: str) -> EvidenceReference:
    detail = "journal event"
    if event.priority is not None:
        detail += f" priority {event.priority}"
    return EvidenceReference(
        kind=EvidenceKind.JOURNAL_EVENT,
        evidence_id=f"journal:{event.cursor}",
        observed_at=event.timestamp,
        subject_type=SubjectType.SERVICE,
        subject_id=service_name,
        reason=EvidenceRelation.SERVICE_UNIT_MATCH,
        summary=_bounded_summary(detail),
        quality_limitations=_bounded_limitations(set(event.warnings)),
        fact=EvidenceFact.JOURNAL_UNIT_EVENT,
    )


def _nearby_process_evidence(
    changes: tuple[TimedProcessChange, ...],
    anchor: datetime,
    window: timedelta,
    service_name: str,
) -> list[EvidenceReference]:
    nearby = _time_slice(changes, anchor, window, lambda item: item.observed_at)
    return [
        _process_evidence(item, service_name)
        for item in nearby
        if _usable_process_change(item.change)
    ]


def _nearby_event_evidence(
    events: tuple[EventObservation, ...],
    anchor: datetime,
    window: timedelta,
    service_name: str,
) -> list[EvidenceReference]:
    nearby = _time_slice(events, anchor, window, lambda item: item.timestamp)
    return [_event_evidence(item, service_name) for item in nearby]


def _time_slice(items, anchor: datetime, window: timedelta, timestamp):
    times = [timestamp(item) for item in items]
    lower = _window_bound(anchor, window, subtract=True)
    upper = _window_bound(anchor, window, subtract=False)
    return items[bisect_left(times, lower):bisect_right(times, upper)]


def _deduplicate_service_changes(
    changes: Iterable[TimedServiceChange],
) -> tuple[TimedServiceChange, ...]:
    output: dict[str, TimedServiceChange] = {}
    for item in changes:
        if not isinstance(item, TimedServiceChange):
            raise TypeError("service changes must contain TimedServiceChange values")
        evidence_id = _service_change_identity(item)
        prior = output.get(evidence_id)
        if prior is not None and prior != item:
            raise ValueError("conflicting duplicate service evidence identity")
        output[evidence_id] = item
    return tuple(sorted(output.values(), key=lambda item: (
        item.observed_at, item.change.name, _service_change_identity(item),
    )))


def _deduplicate_process_changes(
    changes: Iterable[TimedProcessChange],
) -> tuple[TimedProcessChange, ...]:
    output: dict[str, TimedProcessChange] = {}
    for item in changes:
        if not isinstance(item, TimedProcessChange):
            raise TypeError("process changes must contain TimedProcessChange values")
        if item.change.lifetime_id is None:
            continue
        identity = _digest(
            "process-change-v1", item.change.lifetime_id, item.observed_at.isoformat(),
            item.change.lifecycle.value,
        )
        prior = output.get(identity)
        if prior is not None and prior != item:
            raise ValueError("conflicting duplicate process evidence identity")
        output[identity] = item
    return tuple(sorted(output.values(), key=lambda item: (
        item.observed_at, item.change.lifetime_id or "", item.change.lifecycle.value,
    )))


def _deduplicate_events(events: Iterable[EventObservation]) -> tuple[EventObservation, ...]:
    output: dict[str, EventObservation] = {}
    for event in events:
        if not isinstance(event, EventObservation):
            raise TypeError("journal events must contain EventObservation values")
        if not isinstance(event.cursor, str) or not event.cursor or len(event.cursor) > 1024:
            raise ValueError("journal evidence requires a bounded nonempty cursor identity")
        _require_utc(event.timestamp, "journal event timestamp")
        prior = output.get(event.cursor)
        if prior is not None and prior != event:
            raise ValueError("conflicting duplicate journal cursor")
        output[event.cursor] = event
    related = (event for event in output.values() if _valid_service_name(event.unit or ""))
    return tuple(sorted(related, key=lambda item: (item.timestamp, item.cursor)))


def _association_index(
    associations: Iterable[ServiceProcessAssociation],
) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, set[str]] = {}
    for association in associations:
        if not isinstance(association, ServiceProcessAssociation):
            raise TypeError("associations must contain ServiceProcessAssociation values")
        grouped.setdefault(association.service_name, set()).add(association.process_lifetime_id)
    return {name: tuple(sorted(identities)) for name, identities in grouped.items()}


def _process_index(
    changes: tuple[TimedProcessChange, ...],
) -> dict[str, tuple[TimedProcessChange, ...]]:
    grouped: dict[str, list[TimedProcessChange]] = {}
    for item in changes:
        if item.change.lifetime_id is not None:
            grouped.setdefault(item.change.lifetime_id, []).append(item)
    return {identity: tuple(items) for identity, items in grouped.items()}


def _event_index(events: tuple[EventObservation, ...]) -> dict[str, tuple[EventObservation, ...]]:
    grouped: dict[str, list[EventObservation]] = {}
    for event in events:
        if event.unit is not None:
            grouped.setdefault(event.unit, []).append(event)
    return {unit: tuple(items) for unit, items in grouped.items()}


def _usable_process_change(change: ProcessChange) -> bool:
    if change.lifetime_id is None:
        return False
    if change.lifecycle is ProcessLifecycle.NEW:
        return change.previous is None and _process_identity(change.current) == change.lifetime_id
    if change.lifecycle is ProcessLifecycle.EXITED:
        return change.current is None and _process_identity(change.previous) == change.lifetime_id
    if change.lifecycle is ProcessLifecycle.IDENTITY_CHANGED:
        return _process_identity(change.current) == change.lifetime_id
    return False


def _service_state(observation) -> str:
    if observation is None:
        return "absent"
    return f"{observation.load_state}/{observation.active_state}/{observation.sub_state}"


def _service_fact(change: ServiceChange) -> EvidenceFact:
    if change.lifecycle is ServiceLifecycle.REMOVED:
        return EvidenceFact.SERVICE_REMOVED
    return {
        "failed": EvidenceFact.SERVICE_FAILED,
        "inactive": EvidenceFact.SERVICE_INACTIVE,
        "deactivating": EvidenceFact.SERVICE_DEACTIVATING,
    }[change.current.active_state]


def _incident_id(service_name: str, anchor_evidence_id: str) -> str:
    return _digest("incident-v1", INCIDENT_RULE_ID, service_name, anchor_evidence_id)


def _service_change_identity(item: TimedServiceChange) -> str:
    change = item.change
    identity = _digest(
        "service-change-v1", change.name, item.observed_at.isoformat(),
        change.lifecycle.value, _service_state(change.previous), _service_state(change.current),
    )
    return f"service:{identity}"


def _digest(*parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return sha256(payload).hexdigest()


def _evidence_order(evidence: EvidenceReference) -> tuple[datetime, str, str]:
    return evidence.observed_at, evidence.kind.value, evidence.evidence_id


def _validate_options(
    window: timedelta,
    max_evidence: int,
    max_inputs: int,
    journal_status: CollectionStatus,
) -> timedelta:
    if not isinstance(window, timedelta):
        raise TypeError("correlation_window must be a timedelta")
    seconds = window.total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("correlation_window must be finite and non-negative")
    if (isinstance(max_evidence, bool) or not isinstance(max_evidence, int)
            or not 2 <= max_evidence <= MAX_EVIDENCE_PER_INCIDENT):
        raise ValueError(
            f"max_evidence must be between 2 and {MAX_EVIDENCE_PER_INCIDENT}"
        )
    if not isinstance(journal_status, CollectionStatus):
        raise TypeError("journal_status must be a CollectionStatus")
    _validate_max_inputs(max_inputs)
    return window


def _validate_max_inputs(max_inputs: int) -> None:
    if (isinstance(max_inputs, bool) or not isinstance(max_inputs, int)
            or not 1 <= max_inputs <= MAX_INPUTS):
        raise ValueError(f"max_inputs must be between 1 and {MAX_INPUTS}")


def _bounded_inputs(values: Iterable, label: str, limit: int) -> tuple:
    materialized = tuple(islice(values, limit + 1))
    if len(materialized) > limit:
        raise ValueError(f"{label} exceed max_inputs")
    return materialized


def _require_utc(value: datetime, label: str) -> None:
    if not _utc(value):
        raise ValueError(f"{label} must be timezone-aware UTC")


def _utc(value: datetime) -> bool:
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() == UTC.utcoffset(value))


def _valid_service_name(name: object) -> bool:
    return (isinstance(name, str) and name.endswith(".service")
            and len(name) > len(".service")
            and len(name) <= MAX_SUBJECT_ID_LENGTH
            and not any(character.isspace() or ord(character) < 32 for character in name))


def _validate_limitations(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise TypeError("quality_limitations must be a tuple")
    if len(values) > MAX_LIMITATIONS_PER_ENTITY or len(values) != len(set(values)):
        raise ValueError("quality_limitations must be unique and bounded")
    if values != tuple(sorted(values)):
        raise ValueError("quality_limitations must use deterministic sorted order")
    if any(not value or len(value) > MAX_LIMITATION_LENGTH for value in values):
        raise ValueError("quality limitation is empty or too long")


def _bounded_summary(value: str) -> str:
    return value[:MAX_SUMMARY_LENGTH]


def _bounded_limitations(values: set[str]) -> tuple[str, ...]:
    ordered = sorted(values)
    if len(ordered) <= MAX_LIMITATIONS_PER_ENTITY:
        return tuple(ordered)
    bounded = {*ordered[:MAX_LIMITATIONS_PER_ENTITY - 1], "quality_limitations_truncated"}
    return tuple(sorted(bounded))


def _window_bound(anchor: datetime, window: timedelta, *, subtract: bool) -> datetime:
    try:
        return anchor - window if subtract else anchor + window
    except OverflowError:
        return datetime.min.replace(tzinfo=UTC) if subtract else datetime.max.replace(tzinfo=UTC)


def _consistent_service_change(change: ServiceChange) -> bool:
    if not _valid_service_name(change.name):
        return False
    observations = tuple(
        observation for observation in (change.previous, change.current) if observation is not None
    )
    return bool(observations) and all(
        observation.name == change.name and all(
            isinstance(value, str) and 0 < len(value) <= 128
            and not any(character.isspace() or ord(character) < 32 for character in value)
            for value in (observation.load_state, observation.active_state, observation.sub_state)
        )
        for observation in observations
    )


def _process_identity(observation) -> str | None:
    return None if observation is None else observation.lifetime_id
