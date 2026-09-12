"""Stable output for continuous runtime cycle diagnostics."""

from __future__ import annotations

from sentinel.application.runtime_models import RuntimeCycle

RUNTIME_SCHEMA_VERSION = "1"


def runtime_cycle_response(cycle: RuntimeCycle) -> dict[str, object]:
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "cycle": {
            "number": cycle.cycle_number,
            "observed_at": cycle.observed_at.isoformat(),
            "started_monotonic": cycle.started_monotonic,
            "completed_monotonic": cycle.completed_monotonic,
            "duration_seconds": cycle.duration_seconds,
            "snapshot_id": cycle.snapshot_id,
            "collection_quality": {
                name: status.value for name, status in cycle.collection_quality
            },
            "observation_counts": dict(cycle.observation_counts),
            "journal_status": cycle.journal_status.value,
            "events_processed": cycle.events_processed,
            "events_correlated": cycle.events_correlated,
            "incident_candidate_ids": list(cycle.incident_candidate_ids),
            "reconciled_candidate_ids": list(cycle.reconciled_candidate_ids),
            "resolved_incident_ids": list(cycle.resolved_incident_ids),
            "limitations": list(cycle.limitations),
        },
    }


def format_runtime_cycle(cycle: RuntimeCycle) -> str:
    statuses = ", ".join(
        f"{name}={status.value}" for name, status in cycle.collection_quality
    )
    limitations = ",".join(cycle.limitations) if cycle.limitations else "none"
    return (
        f"Cycle {cycle.cycle_number} completed in {cycle.duration_seconds:.6f}s; "
        f"snapshot={cycle.snapshot_id}; events={cycle.events_processed}; "
        f"correlated_events={cycle.events_correlated}; "
        f"candidates_reconciled={len(cycle.reconciled_candidate_ids)}; "
        f"resolved={len(cycle.resolved_incident_ids)}; sources=[{statuses}]; "
        f"limitations=[{limitations}]"
    )
