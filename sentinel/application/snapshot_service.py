"""Coordinates independent observations into a quality-preserving snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sentinel.collectors import (collect_cpu, collect_disk, collect_memory, collect_network, collect_processes,
                                 collect_services, collect_system)
from sentinel.models import CollectionResult, SystemSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotCollection:
    snapshot: SystemSnapshot
    collector_results: tuple[tuple[str, CollectionResult[object]], ...]


class SnapshotService:
    """Application boundary: collectors access Linux, this service only composes results."""

    def __init__(self, proc_root: Path = Path("/proc")) -> None:
        self._proc_root = proc_root

    def collect(self) -> SnapshotCollection:
        results: tuple[tuple[str, CollectionResult[object]], ...] = (
            ("system", collect_system(self._proc_root)),
            ("memory", collect_memory(self._proc_root)),
            ("cpu", collect_cpu(self._proc_root)),
            ("processes", collect_processes(self._proc_root)),
            ("disk", collect_disk()),
            ("network", collect_network(self._proc_root)),
            ("services", collect_services()),
        )
        by_name = dict(results)
        warnings = tuple(warning for _, result in results for warning in result.warnings)
        snapshot = SystemSnapshot(
            timestamp=datetime.now(UTC),
            system=by_name["system"].value,
            memory=by_name["memory"].value,
            cpu=by_name["cpu"].value,
            processes=by_name["processes"].value or (),
            disk=by_name["disk"].value or (),
            network=by_name["network"].value or (),
            services=by_name["services"].value or (),
            results=tuple((name, result.status.value) for name, result in results),
            warnings=warnings,
        )
        return SnapshotCollection(snapshot, results)
