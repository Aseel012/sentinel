"""Immutable evidence and incident contracts shared by analysis and storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

MAX_EVIDENCE_PER_INCIDENT = 128
# Journal cursors are capped at 1024 characters; evidence IDs also carry a short
# source namespace so heterogeneous identities cannot collide.
MAX_EVIDENCE_ID_LENGTH = 2048
MAX_LIMITATIONS_PER_ENTITY = 32
MAX_LIMITATION_LENGTH = 256
MAX_RULE_ID_LENGTH = 128
MAX_SUBJECT_ID_LENGTH = 512
MAX_SUMMARY_LENGTH = 512


class SubjectType(StrEnum):
    SERVICE = "service"


class EvidenceKind(StrEnum):
    SERVICE_CHANGE = "service_change"
    PROCESS_CHANGE = "process_change"
    JOURNAL_EVENT = "journal_event"


class EvidenceRelation(StrEnum):
    SERVICE_CHANGE_ANCHOR = "service_change_anchor"
    SERVICE_UNIT_MATCH = "service_unit_match"
    EXPLICIT_PROCESS_ASSOCIATION = "explicit_process_association"


class EvidenceFact(StrEnum):
    UNKNOWN = "unknown"
    SERVICE_FAILED = "service_failed"
    SERVICE_INACTIVE = "service_inactive"
    SERVICE_DEACTIVATING = "service_deactivating"
    SERVICE_REMOVED = "service_removed"
    PROCESS_EXITED = "process_exited"
    PROCESS_STARTED = "process_started"
    PROCESS_IDENTITY_CHANGED = "process_identity_changed"
    JOURNAL_UNIT_EVENT = "journal_unit_event"


class IncidentState(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


def _require_utc(value: datetime, field_name: str) -> None:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != UTC.utcoffset(value)):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def _require_bounded(value: str, field_name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field_name} must contain between 1 and {maximum} characters")


def _validate_limitations(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise ValueError("quality limitations must be a tuple")
    if len(values) > MAX_LIMITATIONS_PER_ENTITY:
        raise ValueError(f"quality limitations cannot exceed {MAX_LIMITATIONS_PER_ENTITY} entries")
    for value in values:
        _require_bounded(value, "quality limitation", MAX_LIMITATION_LENGTH)
    if len(values) != len(set(values)):
        raise ValueError("quality limitations must be unique")
    if values != tuple(sorted(values)):
        raise ValueError("quality limitations must use deterministic sorted order")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A bounded reference to an authoritative fact and its correlation reason."""

    kind: EvidenceKind
    evidence_id: str
    observed_at: datetime
    subject_type: SubjectType
    subject_id: str
    reason: EvidenceRelation
    summary: str | None = None
    quality_limitations: tuple[str, ...] = ()
    fact: EvidenceFact = EvidenceFact.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind) or not isinstance(self.reason, EvidenceRelation):
            raise ValueError("kind and reason must use incident evidence enums")
        if not isinstance(self.fact, EvidenceFact):
            raise ValueError("fact must use EvidenceFact")
        if not isinstance(self.subject_type, SubjectType):
            raise ValueError("subject_type must use SubjectType")
        _require_bounded(self.evidence_id, "evidence_id", MAX_EVIDENCE_ID_LENGTH)
        _require_utc(self.observed_at, "observed_at")
        _require_bounded(self.subject_id, "subject_id", MAX_SUBJECT_ID_LENGTH)
        if self.summary is not None and (not isinstance(self.summary, str)
                                         or len(self.summary) > MAX_SUMMARY_LENGTH):
            raise ValueError(f"summary cannot exceed {MAX_SUMMARY_LENGTH} characters")
        expected_relation = {
            EvidenceKind.SERVICE_CHANGE: EvidenceRelation.SERVICE_CHANGE_ANCHOR,
            EvidenceKind.PROCESS_CHANGE: EvidenceRelation.EXPLICIT_PROCESS_ASSOCIATION,
            EvidenceKind.JOURNAL_EVENT: EvidenceRelation.SERVICE_UNIT_MATCH,
        }[self.kind]
        if self.reason is not expected_relation:
            raise ValueError("evidence kind and correlation reason are incompatible")
        fact_kind = {
            EvidenceFact.SERVICE_FAILED: EvidenceKind.SERVICE_CHANGE,
            EvidenceFact.SERVICE_INACTIVE: EvidenceKind.SERVICE_CHANGE,
            EvidenceFact.SERVICE_DEACTIVATING: EvidenceKind.SERVICE_CHANGE,
            EvidenceFact.SERVICE_REMOVED: EvidenceKind.SERVICE_CHANGE,
            EvidenceFact.PROCESS_EXITED: EvidenceKind.PROCESS_CHANGE,
            EvidenceFact.PROCESS_STARTED: EvidenceKind.PROCESS_CHANGE,
            EvidenceFact.PROCESS_IDENTITY_CHANGED: EvidenceKind.PROCESS_CHANGE,
            EvidenceFact.JOURNAL_UNIT_EVENT: EvidenceKind.JOURNAL_EVENT,
        }.get(self.fact)
        if fact_kind is not None and fact_kind is not self.kind:
            raise ValueError("evidence kind and structured fact are incompatible")
        _validate_limitations(self.quality_limitations)


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    """Derived incident state containing structured references, never raw observations."""

    incident_id: str
    rule_id: str
    subject_type: SubjectType
    subject_id: str
    state: IncidentState
    started_at: datetime
    last_observed_at: datetime
    resolved_at: datetime | None
    evidence: tuple[EvidenceReference, ...]
    quality_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (not isinstance(self.incident_id, str) or len(self.incident_id) != 64
                or any(char not in "0123456789abcdef" for char in self.incident_id)):
            raise ValueError("incident_id must be a lowercase SHA-256 hexadecimal identity")
        if not isinstance(self.subject_type, SubjectType) or not isinstance(self.state, IncidentState):
            raise ValueError("subject_type and state must use incident enums")
        _require_bounded(self.rule_id, "rule_id", MAX_RULE_ID_LENGTH)
        _require_bounded(self.subject_id, "subject_id", MAX_SUBJECT_ID_LENGTH)
        _require_utc(self.started_at, "started_at")
        _require_utc(self.last_observed_at, "last_observed_at")
        if self.last_observed_at < self.started_at:
            raise ValueError("last_observed_at cannot precede started_at")
        if self.state is IncidentState.ACTIVE and self.resolved_at is not None:
            raise ValueError("active incidents cannot have resolved_at")
        if self.state is IncidentState.RESOLVED:
            if self.resolved_at is None:
                raise ValueError("resolved incidents require resolved_at")
            _require_utc(self.resolved_at, "resolved_at")
            if self.resolved_at < self.last_observed_at:
                raise ValueError("resolved_at cannot precede last_observed_at")
        if not self.evidence:
            raise ValueError("an incident requires evidence")
        if not isinstance(self.evidence, tuple) or any(
                not isinstance(item, EvidenceReference) for item in self.evidence):
            raise ValueError("evidence must be a tuple of EvidenceReference values")
        if len(self.evidence) > MAX_EVIDENCE_PER_INCIDENT:
            raise ValueError(f"evidence cannot exceed {MAX_EVIDENCE_PER_INCIDENT} references")
        identities = tuple((item.kind, item.evidence_id) for item in self.evidence)
        if len(identities) != len(set(identities)):
            raise ValueError("incident evidence identities must be unique")
        anchors = tuple(item for item in self.evidence
                        if item.reason is EvidenceRelation.SERVICE_CHANGE_ANCHOR)
        if len(anchors) != 1:
            raise ValueError("an incident requires exactly one service-change anchor")
        if anchors[0].observed_at != self.started_at:
            raise ValueError("started_at must equal the service-change anchor time")
        if len({item.kind for item in self.evidence}) < 2:
            raise ValueError("an incident requires correlated evidence of at least two kinds")
        if any(item.subject_type is not self.subject_type or item.subject_id != self.subject_id
               for item in self.evidence):
            raise ValueError("incident evidence must reference the incident subject")
        if any(item.observed_at > self.last_observed_at for item in self.evidence):
            raise ValueError("incident evidence cannot follow last_observed_at")
        _validate_limitations(self.quality_limitations)
