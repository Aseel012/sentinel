from datetime import UTC, datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import (CPUObservation, CollectionResult, CollectionStatus, DiskObservation, MemoryObservation,
                             NetworkObservation, ProcessObservation, ServiceObservation, SystemObservation,
                             SystemSnapshot)
from sentinel.storage.database import database_connection
from sentinel.storage.migrations import initialize_schema
from sentinel.storage.retention import delete_snapshots_before
from sentinel.storage.snapshots import SnapshotRepository


def collection(*, partial_processes: bool = False) -> SnapshotCollection:
    timestamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    system = SystemObservation("host", "Linux", "6.0", "x86_64", 15.5)
    memory = MemoryObservation(100, 60, 20, None, 30, 0, 0)
    cpu = CPUObservation(1, 2, 3, 4, 5, 6, 7, 8)
    process = ProcessObservation(42, 1, "worker", "S", 10, None, 2, 3, 99,
                                 "worker --token=[REDACTED]", "/usr/bin/worker")
    disk = DiskObservation("/", 100, 50, 50)
    network = NetworkObservation("eth0", 100, 200, None, None, 0, 0, 0, 0)
    service = ServiceObservation("worker.service", "loaded", "active", "running")
    process_status = CollectionStatus.PARTIAL if partial_processes else CollectionStatus.SUCCESS
    results = (
        ("system", CollectionResult(system, CollectionStatus.SUCCESS, timestamp, 0.1)),
        ("memory", CollectionResult(memory, CollectionStatus.SUCCESS, timestamp, 0.1)),
        ("cpu", CollectionResult(cpu, CollectionStatus.SUCCESS, timestamp, 0.1)),
        ("processes", CollectionResult((process,), process_status, timestamp, 0.1,
            "processes_partial" if partial_processes else None, None,
            ("process 43 disappeared during collection",) if partial_processes else ())),
        ("disk", CollectionResult((disk,), CollectionStatus.SUCCESS, timestamp, 0.1)),
        ("network", CollectionResult((network,), CollectionStatus.SUCCESS, timestamp, 0.1)),
        ("services", CollectionResult((service,), CollectionStatus.SUCCESS, timestamp, 0.1)),
    )
    return SnapshotCollection(SystemSnapshot(timestamp, system, memory, cpu, (process,), (disk,), (network,),
                               (service,), tuple((name, result.status.value) for name, result in results),
                               tuple(warning for _, result in results for warning in result.warnings)), results)


class SnapshotRepositoryTests(unittest.TestCase):
    def repository(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        context = database_connection(Path(directory.name) / "sentinel.db")
        connection = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        initialize_schema(connection)
        return connection, SnapshotRepository(connection)

    def test_round_trip_preserves_observations_and_collection_quality(self) -> None:
        _, repository = self.repository()
        snapshot_id = repository.save(collection(partial_processes=True))
        stored = repository.get(snapshot_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        restored = stored.collection
        self.assertEqual(stored.id, snapshot_id)
        self.assertEqual(restored.snapshot.timestamp, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
        self.assertEqual(restored.snapshot.processes[0].lifetime_id, "42:99")
        self.assertIsNone(restored.snapshot.memory.buffers_bytes)
        self.assertEqual(restored.snapshot.network[0].receive_packets, None)
        processes = dict(restored.collector_results)["processes"]
        self.assertIs(processes.status, CollectionStatus.PARTIAL)
        self.assertEqual(processes.warnings, ("process 43 disappeared during collection",))

    def test_missing_snapshot_returns_none_and_invalid_identity_is_rejected(self) -> None:
        _, repository = self.repository()
        self.assertIsNone(repository.get(1))
        with self.assertRaises(ValueError):
            repository.get(0)

    def test_rejects_duplicate_or_inconsistent_collection_metadata(self) -> None:
        _, repository = self.repository()
        valid = collection()
        duplicate_results = SnapshotCollection(valid.snapshot, valid.collector_results + (valid.collector_results[0],))
        with self.assertRaises(ValueError):
            repository.save(duplicate_results)
        bad_snapshot = SystemSnapshot(valid.snapshot.timestamp, valid.snapshot.system, valid.snapshot.memory,
            valid.snapshot.cpu, valid.snapshot.processes, valid.snapshot.disk, valid.snapshot.network,
            valid.snapshot.services, valid.snapshot.results[:-1], valid.snapshot.warnings)
        with self.assertRaises(ValueError):
            repository.save(SnapshotCollection(bad_snapshot, valid.collector_results))

    def test_failed_child_insert_rolls_back_entire_snapshot(self) -> None:
        connection, repository = self.repository()
        invalid = collection()
        duplicate = invalid.snapshot.processes[0]
        broken_snapshot = SystemSnapshot(invalid.snapshot.timestamp, invalid.snapshot.system, invalid.snapshot.memory,
            invalid.snapshot.cpu, (duplicate, duplicate), invalid.snapshot.disk, invalid.snapshot.network,
            invalid.snapshot.services, invalid.snapshot.results, invalid.snapshot.warnings)
        broken = SnapshotCollection(broken_snapshot, invalid.collector_results)
        with self.assertRaises(sqlite3.IntegrityError):
            repository.save(broken)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 0)

    def test_manual_retention_cascades_children_without_selecting_a_policy(self) -> None:
        connection, repository = self.repository()
        snapshot_id = repository.save(collection())
        removed = delete_snapshots_before(connection, datetime(2026, 1, 3, tzinfo=UTC))
        self.assertEqual(removed, 1)
        self.assertIsNone(repository.get(snapshot_id))
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM process_observations").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            delete_snapshots_before(connection, datetime(2026, 1, 3))
