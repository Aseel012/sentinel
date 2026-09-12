"""Deterministic, privacy-bounded incident list and inspection formatting."""

from __future__ import annotations

from collections.abc import Iterable

from sentinel.application.inspection_service import IncidentInspection
from sentinel.cli.diagnosis_output import diagnosis_payload
from sentinel.models import EvidenceFact, IncidentCandidate

INCIDENT_INSPECTION_SCHEMA_VERSION = "1"


def _fact_groups(
    inspection: IncidentInspection,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    established = tuple(sorted({item.fact.value for item in inspection.incident.evidence
                                if item.fact is not EvidenceFact.UNKNOWN}))
    unknown = tuple(item.evidence_id for item in inspection.incident.evidence
                    if item.fact is EvidenceFact.UNKNOWN)
    not_established = tuple(sorted({*inspection.diagnosis.not_established,
                                    "current_system_state"}))
    return established, unknown, not_established


def _section(title: str, values: Iterable[str]) -> list[str]:
    lines = [f"- {value}" for value in values]
    return [title, *(lines or ["- none"])]


def _incident_payload(incident: IncidentCandidate) -> dict[str, object]:
    return {
        "incident_id": incident.incident_id,
        "state": incident.state.value,
        "subject": {
            "type": incident.subject_type.value,
            "id": incident.subject_id,
        },
        "rule_id": incident.rule_id,
        "opened_at": incident.started_at.isoformat(),
        "last_observed_at": incident.last_observed_at.isoformat(),
        "resolved_at": None if incident.resolved_at is None else incident.resolved_at.isoformat(),
    }


def incident_list_response(
    incidents: Iterable[IncidentCandidate],
    *,
    query_limit: int | None = None,
) -> dict[str, object]:
    values = tuple(incidents)
    response: dict[str, object] = {
        "schema_version": INCIDENT_INSPECTION_SCHEMA_VERSION,
        "incidents": [_incident_payload(item) for item in values],
        "count": len(values),
    }
    if query_limit is not None:
        response["query"] = {
            "limit": query_limit,
            "limit_reached": len(values) == query_limit,
        }
    return response


def inspection_response(inspection: IncidentInspection) -> dict[str, object]:
    incident = inspection.incident
    diagnosis = inspection.diagnosis
    established, unknown, not_established = _fact_groups(inspection)
    return {
        "schema_version": INCIDENT_INSPECTION_SCHEMA_VERSION,
        "incident": _incident_payload(incident),
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind.value,
                "fact": item.fact.value,
                "relation": item.reason.value,
                "observed_at": item.observed_at.isoformat(),
                "quality_limitations": list(item.quality_limitations),
            }
            for item in incident.evidence
        ],
        "facts": {
            "established": list(established),
            "unknown_evidence_ids": list(unknown),
            "not_established": list(not_established),
        },
        "historical_related_incident_ids": list(diagnosis.historical_incident_ids),
        "diagnosis": diagnosis_payload(diagnosis),
        "limitations": {
            "incident": list(incident.quality_limitations),
            "diagnosis": list(diagnosis.limitations),
        },
    }


def format_incident_list(
    incidents: Iterable[IncidentCandidate],
    *,
    query_limit: int | None = None,
) -> str:
    values = tuple(incidents)
    lines = ["Sentinel Incidents"]
    if query_limit is not None:
        lines.append(f"Showing {len(values)} incident(s); query limit={query_limit}.")
    if not values:
        return "\n".join((*lines, "No remembered incidents."))
    lines.extend(
        f"{item.incident_id}  {item.state.value}  {item.subject_type.value}:"
        f"{item.subject_id}  opened={item.started_at.isoformat()}  "
        f"resolved={item.resolved_at.isoformat() if item.resolved_at else '-'}"
        for item in values
    )
    return "\n".join(lines)


def format_inspection(inspection: IncidentInspection) -> str:
    incident = inspection.incident
    diagnosis = inspection.diagnosis
    established, unknown, not_established = _fact_groups(inspection)
    lines = [
        "Sentinel Incident Inspection",
        f"Incident: {incident.incident_id}",
        f"Lifecycle: {incident.state.value}",
        f"Subject: {incident.subject_type.value}:{incident.subject_id}",
        f"Opened: {incident.started_at.isoformat()}",
        f"Last observed: {incident.last_observed_at.isoformat()}",
        f"Resolved: {incident.resolved_at.isoformat() if incident.resolved_at else '-'}",
        "Lifecycle is recorded historical incident state; it does not establish current system state.",
        "Evidence:",
        *(f"- {item.kind.value}:{item.evidence_id} fact={item.fact.value} "
          f"relation={item.reason.value} at={item.observed_at.isoformat()}"
          for item in incident.evidence),
        *_section("Established facts:", established),
        *_section("Unknown evidence:", unknown),
        "Diagnosis:",
        f"- state={diagnosis.state.value} rule={diagnosis.rule.value}",
        f"- {diagnosis.explanation}",
        *_section("Historical related incidents:", diagnosis.historical_incident_ids),
        *_section("Incident limitations:", incident.quality_limitations),
        *_section("Diagnosis limitations:", diagnosis.limitations),
        *_section("Not established:", not_established),
    ]
    return "\n".join(lines)
