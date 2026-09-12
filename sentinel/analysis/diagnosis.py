"""Deterministic explanations derived from bounded incident evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import Iterable

from sentinel.models import (
    EvidenceFact,
    EvidenceReference,
    EvidenceRelation,
    IncidentCandidate,
)
from sentinel.models.incidents import (
    MAX_LIMITATIONS_PER_ENTITY,
    MAX_LIMITATION_LENGTH,
    MAX_SUBJECT_ID_LENGTH,
)

MAX_HISTORICAL_INCIDENTS = 20
MAX_HISTORY_INPUTS = 1_000


class DiagnosisState(StrEnum):
    SUPPORTED = "supported"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNKNOWN = "unknown"


class DiagnosticRule(StrEnum):
    SERVICE_FAILURE_JOURNAL_PROCESS_EXIT = "service_failure_journal_process_exit_v1"
    SERVICE_FAILURE_JOURNAL = "service_failure_journal_v1"
    SERVICE_FAILURE_PROCESS_EXIT = "service_failure_process_exit_v1"
    SERVICE_FAILURE_INSUFFICIENT = "service_failure_insufficient_v1"
    UNSUPPORTED_SERVICE_CONDITION = "unsupported_service_condition_v1"


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """A bounded explanation, evidence provenance, and explicit uncertainty."""

    incident_id: str
    subject_id: str
    state: DiagnosisState
    rule: DiagnosticRule
    explanation: str
    supported_facts: tuple[str, ...]
    provenance: tuple[EvidenceReference, ...]
    historical_incident_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    not_established: tuple[str, ...] = ("causal_direction", "exact_root_cause")

    def __post_init__(self) -> None:
        if (not isinstance(self.incident_id, str) or len(self.incident_id) != 64
                or any(char not in "0123456789abcdef" for char in self.incident_id)):
            raise ValueError("diagnosis incident_id must be a lowercase SHA-256 identity")
        if (not isinstance(self.subject_id, str) or not self.subject_id
                or len(self.subject_id) > MAX_SUBJECT_ID_LENGTH):
            raise ValueError(
                f"diagnosis subject_id must contain between 1 and {MAX_SUBJECT_ID_LENGTH} characters"
            )
        if not isinstance(self.state, DiagnosisState) or not isinstance(self.rule, DiagnosticRule):
            raise TypeError("diagnosis state and rule must use their enums")
        if not self.explanation or len(self.explanation) > 2_048:
            raise ValueError("diagnosis explanation must contain at most 2048 characters")
        for values, maximum, label in (
            (self.supported_facts, 16, "supported facts"),
            (self.provenance, 128, "provenance"),
            (self.historical_incident_ids, MAX_HISTORICAL_INCIDENTS, "history"),
            (self.limitations, MAX_LIMITATIONS_PER_ENTITY, "limitations"),
            (self.not_established, 16, "not_established"),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise ValueError(f"{label} must be a bounded tuple")
        if any(not isinstance(item, EvidenceReference) for item in self.provenance):
            raise TypeError("diagnosis provenance must contain EvidenceReference values")
        if len(self.provenance) != len({(item.kind, item.evidence_id) for item in self.provenance}):
            raise ValueError("diagnosis provenance identities must be unique")
        if any(item.subject_id != self.subject_id for item in self.provenance):
            raise ValueError("diagnosis provenance must reference its subject")
        if (len(self.historical_incident_ids) != len(set(self.historical_incident_ids))
                or any(not isinstance(value, str) or len(value) != 64
                       or any(char not in "0123456789abcdef" for char in value)
                       for value in self.historical_incident_ids)):
            raise ValueError("diagnosis history must contain unique incident identities")
        for values, label in (
            (self.supported_facts, "supported facts"),
            (self.limitations, "limitations"),
            (self.not_established, "not_established"),
        ):
            if any(not isinstance(value, str) or not value or len(value) > MAX_LIMITATION_LENGTH
                   for value in values):
                raise ValueError(f"diagnosis {label} values must be bounded nonempty strings")
        if self.limitations != tuple(sorted(set(self.limitations))):
            raise ValueError("diagnosis limitations must be unique and sorted")


def diagnose_incident(
    incident: IncidentCandidate,
    *,
    historical_incidents: Iterable[IncidentCandidate] = (),
) -> Diagnosis:
    """Apply the strongest supported rule without parsing summaries or raw messages."""
    if not isinstance(incident, IncidentCandidate):
        raise TypeError("incident must be an IncidentCandidate")
    history = _bounded_history(incident, historical_incidents)
    anchor = next(item for item in incident.evidence
                  if item.reason is EvidenceRelation.SERVICE_CHANGE_ANCHOR)
    journal = tuple(item for item in incident.evidence
                    if item.fact is EvidenceFact.JOURNAL_UNIT_EVENT)
    process_exits = tuple(item for item in incident.evidence
                          if item.fact is EvidenceFact.PROCESS_EXITED)

    if anchor.fact is not EvidenceFact.SERVICE_FAILED:
        state = DiagnosisState.UNKNOWN
        rule = DiagnosticRule.UNSUPPORTED_SERVICE_CONDITION
        selected = (anchor,)
        facts = (anchor.fact.value,)
        limitation = ("service_condition_not_supported_by_diagnostic_rules"
                      if anchor.fact is not EvidenceFact.UNKNOWN else "legacy_evidence_fact_unknown")
        explanation = f"{incident.subject_id} has an incident candidate, but its opening condition " \
                      "is not established as a service failure by the available structured evidence."
        extra_limitations = {limitation}
    elif journal and process_exits:
        state = DiagnosisState.SUPPORTED
        rule = DiagnosticRule.SERVICE_FAILURE_JOURNAL_PROCESS_EXIT
        selected = (anchor, *journal, *process_exits)
        facts = ("service_failed", "exact_unit_journal_event", "associated_process_lifetime_exited")
        explanation = f"{incident.subject_id} entered a failed state. An exact-unit journal event " \
                      "and an explicitly associated process lifetime termination occurred within " \
                      "the incident evidence interval."
        extra_limitations = set()
    elif journal:
        state = DiagnosisState.SUPPORTED
        rule = DiagnosticRule.SERVICE_FAILURE_JOURNAL
        selected = (anchor, *journal)
        facts = ("service_failed", "exact_unit_journal_event")
        explanation = f"{incident.subject_id} entered a failed state and an exact-unit journal " \
                      "event occurred within the incident evidence interval."
        extra_limitations = {"associated_process_termination_not_observed"}
    elif process_exits:
        state = DiagnosisState.SUPPORTED
        rule = DiagnosticRule.SERVICE_FAILURE_PROCESS_EXIT
        selected = (anchor, *process_exits)
        facts = ("service_failed", "associated_process_lifetime_exited")
        explanation = f"{incident.subject_id} entered a failed state and an explicitly associated " \
                      "process lifetime ended within the incident evidence interval."
        extra_limitations = {"exact_unit_journal_evidence_not_observed"}
    else:
        state = DiagnosisState.INSUFFICIENT_EVIDENCE
        rule = DiagnosticRule.SERVICE_FAILURE_INSUFFICIENT
        selected = (anchor,)
        facts = ("service_failed",)
        explanation = f"{incident.subject_id} entered a failed state, but the incident contains no " \
                      "exact-unit journal event or explicitly associated process termination."
        extra_limitations = {"insufficient_diagnostic_evidence"}

    if history:
        count = len(history)
        facts = (*facts, f"prior_incident_candidates:{count}")
        explanation += f" {count} earlier incident candidate{'s' if count != 1 else ''} " \
                       "for the same service are recorded; this does not establish a recurring cause."
    explanation += " These facts satisfy deterministic relationship rules. The exact root cause " \
                   "and causal direction are not established."
    limitations = set(incident.quality_limitations) | extra_limitations
    limitations.update(value for item in selected for value in item.quality_limitations)
    if any("partial" in value or "unavailable" in value or "denied" in value
           or "failure" in value or "unsupported" in value
           or "invalid" in value or "unknown" in value for value in limitations):
        explanation += " Evidence context is incomplete, so unobserved facts may change interpretation."
    return Diagnosis(
        incident.incident_id,
        incident.subject_id,
        state,
        rule,
        explanation,
        tuple(facts),
        tuple(sorted(set(selected), key=lambda item: (item.observed_at, item.kind.value, item.evidence_id))),
        tuple(item.incident_id for item in history),
        _bounded_limitations(limitations),
    )


def _bounded_history(
    incident: IncidentCandidate,
    values: Iterable[IncidentCandidate],
) -> tuple[IncidentCandidate, ...]:
    materialized = tuple(islice(values, MAX_HISTORY_INPUTS + 1))
    if len(materialized) > MAX_HISTORY_INPUTS:
        raise ValueError("historical incident input exceeds its bound")
    by_id: dict[str, IncidentCandidate] = {}
    for item in materialized:
        if not isinstance(item, IncidentCandidate):
            raise TypeError("historical incidents must contain IncidentCandidate values")
        previous = by_id.get(item.incident_id)
        if previous is not None and previous != item:
            raise ValueError("conflicting historical incident identity")
        by_id[item.incident_id] = item
    relevant = [item for item in by_id.values()
                if item.incident_id != incident.incident_id
                and item.subject_type is incident.subject_type
                and item.subject_id == incident.subject_id
                and item.started_at < incident.started_at]
    return tuple(sorted(relevant, key=lambda item: (item.started_at, item.incident_id))[-MAX_HISTORICAL_INCIDENTS:])


def _bounded_limitations(values: set[str]) -> tuple[str, ...]:
    clean = sorted(value for value in values if value and len(value) <= MAX_LIMITATION_LENGTH)
    if len(clean) <= MAX_LIMITATIONS_PER_ENTITY:
        return tuple(clean)
    return tuple(sorted((*clean[:MAX_LIMITATIONS_PER_ENTITY - 1], "diagnosis_limitations_truncated")))
