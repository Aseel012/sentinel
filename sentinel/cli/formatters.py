"""Presentation formatting kept separate from collection and application services."""

from __future__ import annotations

from sentinel.application.snapshot_service import SnapshotCollection


def format_snapshot(collection: SnapshotCollection) -> str:
    snapshot = collection.snapshot
    system = snapshot.system
    lines = ["Sentinel", f"Timestamp: {snapshot.timestamp.isoformat()}"]
    if system is not None:
        lines += [f"Machine: {system.hostname}", f"Kernel: {system.kernel}", f"Uptime: {system.uptime_seconds:.0f}s"]
    if snapshot.memory is not None:
        lines.append(f"Memory available: {snapshot.memory.available_bytes} B")
    lines.extend(f"{name}: {status}" for name, status in snapshot.results)
    if snapshot.warnings:
        lines.append(f"Warnings: {len(snapshot.warnings)}")
    return "\n".join(lines)
