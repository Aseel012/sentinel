"""Explicit human and JSON formatting for deterministic diagnoses."""

from __future__ import annotations

from sentinel.analysis.diagnosis import Diagnosis

DIAGNOSIS_SCHEMA_VERSION = "1"


def diagnosis_payload(diagnosis: Diagnosis) -> dict[str, object]:
    """Serialize one diagnosis for reuse by dedicated and inspection output."""
    return {
        "incident_id": diagnosis.incident_id,
        "subject_id": diagnosis.subject_id,
        "state": diagnosis.state.value,
        "rule": diagnosis.rule.value,
        "explanation": diagnosis.explanation,
        "supported_facts": list(diagnosis.supported_facts),
        "provenance": [
            {
                "kind": item.kind.value,
                "evidence_id": item.evidence_id,
                "observed_at": item.observed_at.isoformat(),
                "relation": item.reason.value,
                "fact": item.fact.value,
                "quality_limitations": list(item.quality_limitations),
            }
            for item in diagnosis.provenance
        ],
        "historical_incident_ids": list(diagnosis.historical_incident_ids),
        "limitations": list(diagnosis.limitations),
        "not_established": list(diagnosis.not_established),
    }


def diagnosis_response(diagnosis: Diagnosis) -> dict[str, object]:
    return {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "diagnosis": diagnosis_payload(diagnosis),
    }


def format_diagnosis(diagnosis: Diagnosis) -> str:
    limitation_lines = ([f"- {value}" for value in diagnosis.limitations]
                        if diagnosis.limitations else ["- none"])
    lines = [
        "Sentinel Diagnosis",
        f"Incident: {diagnosis.incident_id}",
        f"Subject: {diagnosis.subject_id}",
        f"State: {diagnosis.state.value}",
        f"Rule: {diagnosis.rule.value}",
        f"Explanation: {diagnosis.explanation}",
        "Supported facts:",
        *(f"- {value}" for value in diagnosis.supported_facts),
        "Provenance:",
        *(f"- {item.kind.value}:{item.evidence_id} {item.fact.value} "
          f"at {item.observed_at.isoformat()}" for item in diagnosis.provenance),
        "Limitations:",
        *limitation_lines,
        "Not established:",
        *(f"- {value}" for value in diagnosis.not_established),
    ]
    return "\n".join(lines)
