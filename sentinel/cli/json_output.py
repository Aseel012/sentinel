"""Versioned machine-readable contracts for the command-line interface."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import (CPUObservation, CollectionResult, DiskObservation, MemoryObservation,
                             NetworkObservation, ProcessObservation, ServiceObservation, SystemObservation,
                             SystemSnapshot)

STATUS_SCHEMA_VERSION = "1"


def _timestamp(value: datetime) -> str:
    """Render an already-validated UTC timestamp as an ISO-8601 string."""
    return value.isoformat()


def _observation(value: object | None) -> Any:
    """Serialize public observation fields without exposing dataclass implementation details."""
    if value is None:
        return None
    if isinstance(value, MemoryObservation):
        return {"total_bytes": value.total_bytes, "available_bytes": value.available_bytes,
                "free_bytes": value.free_bytes, "buffers_bytes": value.buffers_bytes,
                "cached_bytes": value.cached_bytes, "swap_total_bytes": value.swap_total_bytes,
                "swap_free_bytes": value.swap_free_bytes}
    if isinstance(value, CPUObservation):
        return {"user_ticks": value.user_ticks, "nice_ticks": value.nice_ticks,
                "system_ticks": value.system_ticks, "idle_ticks": value.idle_ticks,
                "iowait_ticks": value.iowait_ticks, "irq_ticks": value.irq_ticks,
                "softirq_ticks": value.softirq_ticks, "steal_ticks": value.steal_ticks}
    if isinstance(value, SystemObservation):
        return {"hostname": value.hostname, "operating_system": value.operating_system,
                "kernel": value.kernel, "architecture": value.architecture,
                "uptime_seconds": value.uptime_seconds}
    if isinstance(value, ProcessObservation):
        return {"pid": value.pid, "ppid": value.ppid, "name": value.name, "state": value.state,
                "rss_bytes": value.rss_bytes, "virtual_memory_bytes": value.virtual_memory_bytes,
                "threads": value.threads, "cpu_time_ticks": value.cpu_time_ticks,
                "start_time_ticks": value.start_time_ticks, "lifetime_id": value.lifetime_id,
                "command": value.command, "executable": value.executable}
    if isinstance(value, DiskObservation):
        return {"path": value.path, "total_bytes": value.total_bytes, "used_bytes": value.used_bytes,
                "free_bytes": value.free_bytes}
    if isinstance(value, NetworkObservation):
        return {"interface": value.interface, "receive_bytes": value.receive_bytes,
                "transmit_bytes": value.transmit_bytes, "receive_packets": value.receive_packets,
                "transmit_packets": value.transmit_packets, "receive_errors": value.receive_errors,
                "transmit_errors": value.transmit_errors, "receive_drops": value.receive_drops,
                "transmit_drops": value.transmit_drops}
    if isinstance(value, ServiceObservation):
        return {"name": value.name, "load_state": value.load_state, "active_state": value.active_state,
                "sub_state": value.sub_state}
    if isinstance(value, (tuple, list)):
        return [_observation(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported public observation type: {type(value).__name__}")


def _snapshot(snapshot: SystemSnapshot) -> dict[str, Any]:
    return {
        "timestamp": _timestamp(snapshot.timestamp),
        "system": _observation(snapshot.system),
        "memory": _observation(snapshot.memory),
        "cpu": _observation(snapshot.cpu),
        "processes": _observation(snapshot.processes),
        "disk": _observation(snapshot.disk),
        "network": _observation(snapshot.network),
        "services": _observation(snapshot.services),
        "collector_statuses": [{"name": name, "status": status} for name, status in snapshot.results],
        "warnings": list(snapshot.warnings),
    }


def _result(result: CollectionResult[object]) -> dict[str, Any]:
    """Serialize collection quality; observation values live once in ``snapshot``.

    Repeating a process list here would double response size and duplicate sensitive,
    albeit redacted, command metadata. The snapshot is the authoritative payload;
    this object preserves the complete collection-result semantics for that payload.
    """
    return {
        "status": result.status.value,
        "collected_at": _timestamp(result.collected_at),
        "duration_seconds": result.duration_seconds,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "warnings": list(result.warnings),
    }


def status_response(collection: SnapshotCollection) -> dict[str, Any]:
    """Return the stable, JSON-ready status response contract (schema version 1)."""
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "snapshot": _snapshot(collection.snapshot),
        "collectors": {name: _result(result) for name, result in collection.collector_results},
    }
