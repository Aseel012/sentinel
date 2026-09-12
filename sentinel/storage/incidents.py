"""Transactional repository for normalized, derived incident state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sentinel.models import (
    EvidenceFact, EvidenceKind, EvidenceReference, EvidenceRelation, IncidentCandidate, IncidentState,
    SubjectType,
)
from sentinel.models.incidents import (
    MAX_EVIDENCE_PER_INCIDENT, MAX_LIMITATIONS_PER_ENTITY, MAX_SUBJECT_ID_LENGTH,
)

from .database import transaction

DEFAULT_INCIDENT_LIMIT = 100
MAX_INCIDENT_LIMIT = 1_000
EVIDENCE_LIMIT_REACHED = "evidence_storage_limit_reached"
LIMITATIONS_LIMIT_REACHED = "quality_limitations_storage_limit_reached"
EVIDENCE_LIMITATIONS_LIMIT_REACHED = "evidence_quality_limitations_storage_limit_reached"


class IncidentStorageError(RuntimeError):
    """Persisted or repeated incident data violates the incident contract."""


@dataclass(frozen=True, slots=True)
class IncidentReconciliation:
    candidate_ids: tuple[str, ...]
    resolved_ids: tuple[str, ...]


def _timestamp(value: datetime, field_name: str = "timestamp") -> str:
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != UTC.utcoffset(value)):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value.isoformat(timespec="microseconds")


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise IncidentStorageError(f"stored {field_name} is malformed")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IncidentStorageError(f"stored {field_name} is malformed") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise IncidentStorageError(f"stored {field_name} is not UTC")
    return timestamp


class IncidentRepository:
    """Persist incident candidates while keeping evidence structured and bounded."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def reconcile(
        self,
        candidates: tuple[IncidentCandidate, ...],
        *,
        resolved_subjects: tuple[tuple[SubjectType, str], ...],
        observed_at: datetime,
        resolution_authoritative: bool,
    ) -> IncidentReconciliation:
        """Upsert candidates and apply only explicit, authoritative resolutions."""
        if not isinstance(resolution_authoritative, bool):
            raise ValueError("resolution_authoritative must be a boolean")
        _timestamp(observed_at, "observed_at")
        unique_candidates = self._unique_candidates(candidates)
        resolved = self._validate_subjects(resolved_subjects)
        active_subjects = {(item.subject_type, item.subject_id) for item in unique_candidates}
        resolved_ids: list[str] = []
        with transaction(self._connection):
            for candidate in unique_candidates:
                if candidate.state is not IncidentState.ACTIVE:
                    raise ValueError("correlation reconciliation accepts active candidates only")
                self._upsert_candidate(candidate)
            if resolution_authoritative:
                for subject_type, subject_id in resolved:
                    if (subject_type, subject_id) in active_subjects:
                        continue
                    rows = self._connection.execute(
                        """SELECT incident_id, last_observed_at FROM incidents
                           WHERE subject_type = ? AND subject_id = ? AND state = 'active'
                           ORDER BY incident_id""",
                        (subject_type.value, subject_id),
                    ).fetchall()
                    for incident_id, last_observed in rows:
                        if observed_at < _parse_timestamp(last_observed, "last_observed_at"):
                            raise ValueError("resolution time cannot precede incident evidence")
                        value = _timestamp(observed_at, "observed_at")
                        self._connection.execute(
                            """UPDATE incidents SET state = 'resolved', last_observed_at = ?, resolved_at = ?
                               WHERE incident_id = ? AND state = 'active'""",
                            (value, value, incident_id),
                        )
                        resolved_ids.append(incident_id)
        return IncidentReconciliation(
            tuple(item.incident_id for item in unique_candidates), tuple(sorted(resolved_ids))
        )

    def get(self, incident_id: str) -> IncidentCandidate | None:
        if (not isinstance(incident_id, str) or len(incident_id) != 64
                or any(char not in "0123456789abcdef" for char in incident_id)):
            raise ValueError("incident_id must be a lowercase SHA-256 hexadecimal identity")
        with transaction(self._connection, mode="DEFERRED"):
            row = self._connection.execute(
                """SELECT incident_id, rule_id, subject_type, subject_id, state, started_at,
                          last_observed_at, resolved_at FROM incidents WHERE incident_id = ?""",
                (incident_id,),
            ).fetchone()
            return None if row is None else self._read_incident(row)

    def list_recent(
        self,
        *,
        limit: int = DEFAULT_INCIDENT_LIMIT,
        state: IncidentState | None = None,
        subject: tuple[SubjectType, str] | None = None,
    ) -> tuple[IncidentCandidate, ...]:
        """Return a bounded, deterministic newest-first incident view."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_INCIDENT_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_INCIDENT_LIMIT}")
        predicates: list[str] = []
        values: list[object] = []
        if state is not None:
            if not isinstance(state, IncidentState):
                raise ValueError("state must be an IncidentState")
            predicates.append("state = ?")
            values.append(state.value)
        if subject is not None:
            subject_type, subject_id = subject
            self._validate_subjects((subject,))
            predicates.extend(("subject_type = ?", "subject_id = ?"))
            values.extend((subject_type.value, subject_id))
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        with transaction(self._connection, mode="DEFERRED"):
            rows = self._connection.execute(
                """SELECT incident_id, rule_id, subject_type, subject_id, state, started_at,
                          last_observed_at, resolved_at FROM incidents""" + where
                + " ORDER BY last_observed_at DESC, incident_id ASC LIMIT ?",
                (*values, limit),
            )
            return tuple(self._read_incident(row) for row in rows)

    @staticmethod
    def _unique_candidates(candidates: tuple[IncidentCandidate, ...]) -> tuple[IncidentCandidate, ...]:
        by_id: dict[str, IncidentCandidate] = {}
        for candidate in candidates:
            previous = by_id.setdefault(candidate.incident_id, candidate)
            if previous != candidate:
                raise ValueError("duplicate incident identities in one reconciliation must be identical")
        return tuple(by_id[key] for key in sorted(by_id))

    @staticmethod
    def _validate_subjects(
        subjects: tuple[tuple[SubjectType, str], ...],
    ) -> tuple[tuple[SubjectType, str], ...]:
        unique: set[tuple[SubjectType, str]] = set()
        for subject_type, subject_id in subjects:
            if not isinstance(subject_type, SubjectType):
                raise ValueError("subject_type must be a SubjectType")
            if not subject_id or len(subject_id) > MAX_SUBJECT_ID_LENGTH:
                raise ValueError("subject_id is empty or exceeds its bound")
            unique.add((subject_type, subject_id))
        return tuple(sorted(unique, key=lambda item: (item[0].value, item[1])))

    def _upsert_candidate(self, candidate: IncidentCandidate) -> None:
        existing = self._connection.execute(
            """SELECT rule_id, subject_type, subject_id, state, started_at,
                      last_observed_at, resolved_at FROM incidents WHERE incident_id = ?""",
            (candidate.incident_id,),
        ).fetchone()
        if existing is None:
            self._connection.execute(
                """INSERT INTO incidents(incident_id, rule_id, subject_type, subject_id, state,
                    started_at, last_observed_at, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate.incident_id, candidate.rule_id, candidate.subject_type.value,
                 candidate.subject_id, candidate.state.value, _timestamp(candidate.started_at),
                 _timestamp(candidate.last_observed_at), None),
            )
        else:
            expected = (candidate.rule_id, candidate.subject_type.value, candidate.subject_id,
                        _timestamp(candidate.started_at))
            if (existing[0], existing[1], existing[2], existing[4]) != expected:
                raise IncidentStorageError("incident identity was reused for different semantic fields")
            prior_last = _parse_timestamp(existing[5], "last_observed_at")
            if existing[3] == IncidentState.RESOLVED.value:
                resolved_at = _parse_timestamp(existing[6], "resolved_at")
                if candidate.last_observed_at > resolved_at:
                    raise IncidentStorageError("new evidence cannot extend an already resolved incident")
            elif candidate.last_observed_at > prior_last:
                self._connection.execute(
                    "UPDATE incidents SET last_observed_at = ? WHERE incident_id = ?",
                    (_timestamp(candidate.last_observed_at), candidate.incident_id),
                )
        self._merge_evidence(candidate)
        self._merge_incident_limitations(candidate.incident_id, candidate.quality_limitations)

    def _merge_evidence(self, candidate: IncidentCandidate) -> None:
        existing = {self._evidence_key(item): item for item in self._read_evidence(candidate.incident_id)}
        for item in candidate.evidence:
            key = self._evidence_key(item)
            existing[key] = item if key not in existing else self._merge_evidence_value(existing[key], item)
        ordered = sorted(existing.values(), key=self._evidence_sort_key)
        truncated = len(ordered) > MAX_EVIDENCE_PER_INCIDENT
        if truncated:
            anchors = [item for item in ordered
                       if item.reason is EvidenceRelation.SERVICE_CHANGE_ANCHOR]
            if len(anchors) != 1:
                raise IncidentStorageError("persisted incident must have exactly one anchor")
            non_anchor = [item for item in ordered if item is not anchors[0]]
            ordered = sorted(
                [anchors[0], *non_anchor[-(MAX_EVIDENCE_PER_INCIDENT - 1):]],
                key=self._evidence_sort_key,
            )
        selected = {self._evidence_key(item) for item in ordered}
        for key in set(existing) - selected:
            self._connection.execute(
                "DELETE FROM incident_evidence WHERE incident_id = ? AND kind = ? AND evidence_id = ?",
                (candidate.incident_id, key[0], key[1]),
            )
        for item in ordered:
            self._connection.execute(
                """INSERT INTO incident_evidence(incident_id, kind, evidence_id, observed_at,
                    subject_type, subject_id, reason, summary, fact) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(incident_id, kind, evidence_id) DO NOTHING""",
                (candidate.incident_id, item.kind.value, item.evidence_id,
                 _timestamp(item.observed_at), item.subject_type.value, item.subject_id,
                 item.reason.value, item.summary, item.fact.value),
            )
            self._connection.execute(
                """UPDATE incident_evidence SET fact = ?
                   WHERE incident_id = ? AND kind = ? AND evidence_id = ?""",
                (item.fact.value, candidate.incident_id, item.kind.value, item.evidence_id),
            )
            self._connection.executemany(
                """INSERT INTO incident_evidence_limitations(
                    incident_id, kind, evidence_id, limitation) VALUES (?, ?, ?, ?)
                    ON CONFLICT DO NOTHING""",
                ((candidate.incident_id, item.kind.value, item.evidence_id, limitation)
                 for limitation in item.quality_limitations),
            )
        if truncated:
            self._merge_incident_limitations(candidate.incident_id, (EVIDENCE_LIMIT_REACHED,))

    def _merge_incident_limitations(self, incident_id: str, limitations: tuple[str, ...]) -> None:
        current = {row[0] for row in self._connection.execute(
            "SELECT limitation FROM incident_limitations WHERE incident_id = ?", (incident_id,)
        )}
        merged = sorted(current | set(limitations))
        if len(merged) > MAX_LIMITATIONS_PER_ENTITY:
            merged = [*merged[:MAX_LIMITATIONS_PER_ENTITY - 1], LIMITATIONS_LIMIT_REACHED]
        self._connection.execute("DELETE FROM incident_limitations WHERE incident_id = ?", (incident_id,))
        self._connection.executemany(
            "INSERT INTO incident_limitations(incident_id, limitation) VALUES (?, ?)",
            ((incident_id, limitation) for limitation in sorted(set(merged))),
        )

    def _read_incident(self, row: tuple[object, ...]) -> IncidentCandidate:
        incident_id = str(row[0])
        try:
            subject_type = SubjectType(str(row[2]))
            state = IncidentState(str(row[4]))
            resolved_at = None if row[7] is None else _parse_timestamp(str(row[7]), "resolved_at")
            return IncidentCandidate(
                incident_id, str(row[1]), subject_type, str(row[3]), state,
                _parse_timestamp(str(row[5]), "started_at"),
                _parse_timestamp(str(row[6]), "last_observed_at"), resolved_at,
                self._read_evidence(incident_id), self._read_limitations(incident_id),
            )
        except ValueError as exc:
            raise IncidentStorageError("stored incident data violates the model contract") from exc

    def _read_evidence(self, incident_id: str) -> tuple[EvidenceReference, ...]:
        limitations_by_key: dict[tuple[str, str], list[str]] = {}
        limitation_rows = self._connection.execute(
            """SELECT kind, evidence_id, limitation FROM incident_evidence_limitations
               WHERE incident_id = ? ORDER BY kind, evidence_id, limitation""",
            (incident_id,),
        )
        for kind, evidence_id, limitation in limitation_rows:
            limitations_by_key.setdefault((kind, evidence_id), []).append(limitation)
        rows = self._connection.execute(
            """SELECT kind, evidence_id, observed_at, subject_type, subject_id, reason, summary, fact
               FROM incident_evidence WHERE incident_id = ?
               ORDER BY observed_at, kind, evidence_id""",
            (incident_id,),
        )
        output: list[EvidenceReference] = []
        for kind, evidence_id, observed_at, subject_type, subject_id, reason, summary, fact in rows:
            limitations = tuple(limitations_by_key.get((kind, evidence_id), ()))
            try:
                output.append(EvidenceReference(
                    EvidenceKind(kind), evidence_id, _parse_timestamp(observed_at, "evidence observed_at"),
                    SubjectType(subject_type), subject_id, EvidenceRelation(reason), summary, limitations,
                    EvidenceFact(fact),
                ))
            except ValueError as exc:
                raise IncidentStorageError("stored incident evidence violates the model contract") from exc
        return tuple(output)

    def _read_limitations(self, incident_id: str) -> tuple[str, ...]:
        return tuple(row[0] for row in self._connection.execute(
            "SELECT limitation FROM incident_limitations WHERE incident_id = ? ORDER BY limitation",
            (incident_id,),
        ))

    @staticmethod
    def _evidence_key(item: EvidenceReference) -> tuple[str, str]:
        return item.kind.value, item.evidence_id

    @staticmethod
    def _evidence_sort_key(item: EvidenceReference) -> tuple[datetime, str, str]:
        return item.observed_at, item.kind.value, item.evidence_id

    @staticmethod
    def _merge_evidence_value(previous: EvidenceReference,
                              current: EvidenceReference) -> EvidenceReference:
        previous_core = replace(previous, quality_limitations=(), fact=EvidenceFact.UNKNOWN)
        current_core = replace(current, quality_limitations=(), fact=EvidenceFact.UNKNOWN)
        if previous_core != current_core:
            raise IncidentStorageError("an evidence identity was reused for different content")
        if (previous.fact is not EvidenceFact.UNKNOWN and current.fact is not EvidenceFact.UNKNOWN
                and previous.fact is not current.fact):
            raise IncidentStorageError("an evidence identity was reused for conflicting facts")
        fact = current.fact if previous.fact is EvidenceFact.UNKNOWN else previous.fact
        limitations = sorted(set(previous.quality_limitations) | set(current.quality_limitations))
        if len(limitations) > MAX_LIMITATIONS_PER_ENTITY:
            limitations = [*limitations[:MAX_LIMITATIONS_PER_ENTITY - 1],
                           EVIDENCE_LIMITATIONS_LIMIT_REACHED]
        return replace(previous, fact=fact, quality_limitations=tuple(sorted(set(limitations))))
