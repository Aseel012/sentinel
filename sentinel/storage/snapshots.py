"""Repository for transactional persistence and retrieval of complete snapshots."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import (CPUObservation, CollectionResult, CollectionStatus, DiskObservation,
                             MemoryObservation, NetworkObservation, ProcessObservation, ServiceObservation,
                             SystemObservation, SystemSnapshot)

from .database import transaction
from .retention import trim_snapshots


class SnapshotStorageError(RuntimeError):
    """Persisted snapshot data does not meet Sentinel's storage contract."""


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    id: int
    collection: SnapshotCollection


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("storage requires timezone-aware UTC timestamps")
    return value.isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SnapshotStorageError("stored timestamp is malformed") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise SnapshotStorageError("stored timestamp is not UTC")
    return timestamp


class SnapshotRepository:
    """Persist snapshots in an existing, initialized SQLite schema.

    Connection ownership remains with the caller.  Each ``save`` uses one explicit
    transaction so a failure cannot leave a snapshot without its children or
    collection-quality data.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, collection: SnapshotCollection, *, max_snapshots: int | None = None) -> int:
        """Persist a full collection and return its SQLite snapshot identity."""
        if max_snapshots is not None and (
            isinstance(max_snapshots, bool) or not isinstance(max_snapshots, int)
            or max_snapshots <= 0
        ):
            raise ValueError("max_snapshots must be a positive integer or None")
        snapshot = collection.snapshot
        result_names = tuple(name for name, _ in collection.collector_results)
        snapshot_statuses = tuple(snapshot.results)
        if len(set(result_names)) != len(result_names):
            raise ValueError("collection result names must be unique")
        if len({name for name, _ in snapshot_statuses}) != len(snapshot_statuses):
            raise ValueError("snapshot collector status names must be unique")
        expected_statuses = {name: result.status.value for name, result in collection.collector_results}
        if dict(snapshot_statuses) != expected_statuses:
            raise ValueError("snapshot collector statuses do not match collection results")
        with transaction(self._connection):
            cursor = self._connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", (_timestamp(snapshot.timestamp),))
            snapshot_id = cursor.lastrowid
            if snapshot_id is None:
                raise SnapshotStorageError("SQLite did not return a snapshot identity")
            self._insert_single_observations(snapshot_id, snapshot)
            self._insert_child_observations(snapshot_id, snapshot)
            self._insert_collection_results(snapshot_id, collection.collector_results)
            if max_snapshots is not None:
                trim_snapshots(self._connection, max_snapshots)
        return snapshot_id

    def get(self, snapshot_id: int) -> StoredSnapshot | None:
        """Load a snapshot by identity, or return ``None`` when it is absent."""
        if snapshot_id <= 0:
            raise ValueError("snapshot_id must be positive")
        with transaction(self._connection, mode="DEFERRED"):
            row = self._connection.execute("SELECT observed_at FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            if row is None:
                return None
            snapshot = SystemSnapshot(
                timestamp=_parse_timestamp(row[0]),
                system=self._read_system(snapshot_id), memory=self._read_memory(snapshot_id), cpu=self._read_cpu(snapshot_id),
                processes=self._read_processes(snapshot_id), disk=self._read_disks(snapshot_id),
                network=self._read_networks(snapshot_id), services=self._read_services(snapshot_id),
                results=(), warnings=(),
            )
            results = self._read_results(snapshot_id, snapshot)
            warnings = tuple(warning for _, result in results for warning in result.warnings)
            finalized = SystemSnapshot(snapshot.timestamp, snapshot.system, snapshot.memory, snapshot.cpu, snapshot.processes,
                                       snapshot.disk, snapshot.network, snapshot.services,
                                       tuple((name, result.status.value) for name, result in results), warnings)
            return StoredSnapshot(snapshot_id, SnapshotCollection(finalized, results))

    def _insert_single_observations(self, snapshot_id: int, snapshot: SystemSnapshot) -> None:
        if snapshot.system is not None:
            item = snapshot.system
            self._connection.execute("""INSERT INTO system_observations(snapshot_id, hostname, operating_system, kernel,
                architecture, uptime_seconds) VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot_id, item.hostname, item.operating_system, item.kernel, item.architecture, item.uptime_seconds))
        if snapshot.memory is not None:
            item = snapshot.memory
            self._connection.execute("""INSERT INTO memory_observations(snapshot_id, total_bytes, available_bytes,
                free_bytes, buffers_bytes, cached_bytes, swap_total_bytes, swap_free_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (snapshot_id, item.total_bytes, item.available_bytes,
                item.free_bytes, item.buffers_bytes, item.cached_bytes, item.swap_total_bytes, item.swap_free_bytes))
        if snapshot.cpu is not None:
            item = snapshot.cpu
            self._connection.execute("""INSERT INTO cpu_observations(snapshot_id, user_ticks, nice_ticks, system_ticks,
                idle_ticks, iowait_ticks, irq_ticks, softirq_ticks, steal_ticks)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (snapshot_id, item.user_ticks, item.nice_ticks,
                item.system_ticks, item.idle_ticks, item.iowait_ticks, item.irq_ticks, item.softirq_ticks, item.steal_ticks))

    def _insert_child_observations(self, snapshot_id: int, snapshot: SystemSnapshot) -> None:
        self._connection.executemany("""INSERT INTO process_observations(snapshot_id, pid, ppid, name, state, rss_bytes,
            virtual_memory_bytes, threads, cpu_time_ticks, start_time_ticks, command, executable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", ((snapshot_id, item.pid, item.ppid, item.name, item.state,
            item.rss_bytes, item.virtual_memory_bytes, item.threads, item.cpu_time_ticks, item.start_time_ticks,
            item.command, item.executable) for item in snapshot.processes))
        self._connection.executemany("""INSERT INTO disk_observations(snapshot_id, path, total_bytes, used_bytes, free_bytes)
            VALUES (?, ?, ?, ?, ?)""", ((snapshot_id, item.path, item.total_bytes, item.used_bytes, item.free_bytes)
            for item in snapshot.disk))
        self._connection.executemany("""INSERT INTO network_observations(snapshot_id, interface, receive_bytes,
            transmit_bytes, receive_packets, transmit_packets, receive_errors, transmit_errors, receive_drops,
            transmit_drops) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", ((snapshot_id, item.interface,
            item.receive_bytes, item.transmit_bytes, item.receive_packets, item.transmit_packets,
            item.receive_errors, item.transmit_errors, item.receive_drops, item.transmit_drops)
            for item in snapshot.network))
        self._connection.executemany("""INSERT INTO service_observations(snapshot_id, name, load_state, active_state,
            sub_state) VALUES (?, ?, ?, ?, ?)""", ((snapshot_id, item.name, item.load_state, item.active_state,
            item.sub_state) for item in snapshot.services))

    def _insert_collection_results(self, snapshot_id: int, results: tuple[tuple[str, CollectionResult[object]], ...]) -> None:
        for name, result in results:
            self._connection.execute("""INSERT INTO collection_results(snapshot_id, collector_name, status, collected_at,
                duration_seconds, error_code, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, name, result.status.value, _timestamp(result.collected_at), result.duration_seconds,
                 result.error_code, result.error_message))
            self._connection.executemany("""INSERT INTO collection_warnings(snapshot_id, collector_name, warning_index,
                warning) VALUES (?, ?, ?, ?)""", ((snapshot_id, name, index, warning)
                for index, warning in enumerate(result.warnings)))

    def _read_system(self, snapshot_id: int) -> SystemObservation | None:
        row = self._connection.execute("SELECT hostname, operating_system, kernel, architecture, uptime_seconds FROM system_observations WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return None if row is None else SystemObservation(*row)

    def _read_memory(self, snapshot_id: int) -> MemoryObservation | None:
        row = self._connection.execute("""SELECT total_bytes, available_bytes, free_bytes, buffers_bytes, cached_bytes,
            swap_total_bytes, swap_free_bytes FROM memory_observations WHERE snapshot_id = ?""", (snapshot_id,)).fetchone()
        return None if row is None else MemoryObservation(*row)

    def _read_cpu(self, snapshot_id: int) -> CPUObservation | None:
        row = self._connection.execute("""SELECT user_ticks, nice_ticks, system_ticks, idle_ticks, iowait_ticks,
            irq_ticks, softirq_ticks, steal_ticks FROM cpu_observations WHERE snapshot_id = ?""", (snapshot_id,)).fetchone()
        return None if row is None else CPUObservation(*row)

    def _read_processes(self, snapshot_id: int) -> tuple[ProcessObservation, ...]:
        rows = self._connection.execute("""SELECT pid, ppid, name, state, rss_bytes, virtual_memory_bytes, threads,
            cpu_time_ticks, start_time_ticks, command, executable FROM process_observations
            WHERE snapshot_id = ? ORDER BY id""", (snapshot_id,))
        return tuple(ProcessObservation(*row) for row in rows)

    def _read_disks(self, snapshot_id: int) -> tuple[DiskObservation, ...]:
        rows = self._connection.execute("SELECT path, total_bytes, used_bytes, free_bytes FROM disk_observations WHERE snapshot_id = ? ORDER BY id", (snapshot_id,))
        return tuple(DiskObservation(*row) for row in rows)

    def _read_networks(self, snapshot_id: int) -> tuple[NetworkObservation, ...]:
        rows = self._connection.execute("""SELECT interface, receive_bytes, transmit_bytes, receive_packets,
            transmit_packets, receive_errors, transmit_errors, receive_drops, transmit_drops FROM network_observations
            WHERE snapshot_id = ? ORDER BY id""", (snapshot_id,))
        return tuple(NetworkObservation(*row) for row in rows)

    def _read_services(self, snapshot_id: int) -> tuple[ServiceObservation, ...]:
        rows = self._connection.execute("SELECT name, load_state, active_state, sub_state FROM service_observations WHERE snapshot_id = ? ORDER BY id", (snapshot_id,))
        return tuple(ServiceObservation(*row) for row in rows)

    def _read_results(self, snapshot_id: int, snapshot: SystemSnapshot) -> tuple[tuple[str, CollectionResult[object]], ...]:
        values: dict[str, object | None] = {"system": snapshot.system, "memory": snapshot.memory, "cpu": snapshot.cpu,
                                              "processes": snapshot.processes, "disk": snapshot.disk,
                                              "network": snapshot.network, "services": snapshot.services}
        rows = self._connection.execute("""SELECT collector_name, status, collected_at, duration_seconds, error_code,
            error_message FROM collection_results WHERE snapshot_id = ? ORDER BY rowid""", (snapshot_id,))
        output = []
        for name, status, collected_at, duration, code, message in rows:
            warning_rows = self._connection.execute("""SELECT warning FROM collection_warnings
                WHERE snapshot_id = ? AND collector_name = ? ORDER BY warning_index""", (snapshot_id, name))
            try:
                collection_status = CollectionStatus(status)
            except ValueError as exc:
                raise SnapshotStorageError("stored collection status is invalid") from exc
            output.append((name, CollectionResult(values.get(name), collection_status, _parse_timestamp(collected_at),
                                                   duration, code, message, tuple(row[0] for row in warning_rows))))
        return tuple(output)
