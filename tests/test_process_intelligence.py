from datetime import UTC, datetime
import unittest

from sentinel.analysis import ComparisonState, ProcessLifecycle, compare_process_collections
from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import CollectionResult, CollectionStatus, ProcessObservation, SystemSnapshot


def process(pid: int, start: int | None, cpu: int | None, rss: int = 10, virtual: int | None = 100) -> ProcessObservation:
    return ProcessObservation(pid, 1, f"p{pid}", "S", rss, virtual, 1, cpu, start, None, None)


def collection(processes: tuple[ProcessObservation, ...], status: CollectionStatus = CollectionStatus.SUCCESS) -> SnapshotCollection:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    result = CollectionResult(processes if status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL) else None,
                              status, timestamp, 0.1)
    snapshot = SystemSnapshot(timestamp, None, None, None, processes if result.value is not None else (), (), (), (),
                              (("processes", status.value),), result.warnings)
    return SnapshotCollection(snapshot, (("processes", result),))


class ProcessIntelligenceTests(unittest.TestCase):
    def test_first_collection_is_insufficient_history_not_new(self) -> None:
        result = compare_process_collections(None, collection((process(1, 10, 3),)), 1.0)
        self.assertEqual(result.changes[0].lifecycle, ProcessLifecycle.INSUFFICIENT_HISTORY)
        self.assertEqual(result.changes[0].cpu_time.state, ComparisonState.NO_PREVIOUS)

    def test_continuing_process_derives_cpu_rss_and_virtual_deltas(self) -> None:
        prior = collection((process(1, 10, 5, 10, 100),))
        current = collection((process(1, 10, 11, 7, 125),))
        change = compare_process_collections(prior, current, 2.5).changes[0]
        self.assertEqual(change.lifecycle, ProcessLifecycle.CONTINUING)
        self.assertEqual(change.cpu_time.rate_per_second, 2.4)
        self.assertEqual(change.rss_bytes_delta, -3)
        self.assertEqual(change.virtual_memory_bytes_delta, 25)

    def test_new_exit_and_identity_change_are_distinct(self) -> None:
        prior = collection((process(1, 10, 1), process(2, 20, 1)))
        current = collection((process(1, 11, 1), process(3, 30, 1)))
        changes = {change.pid: change for change in compare_process_collections(prior, current, 1).changes}
        self.assertEqual(changes[1].lifecycle, ProcessLifecycle.IDENTITY_CHANGED)
        self.assertEqual(changes[3].lifecycle, ProcessLifecycle.NEW)
        self.assertEqual(changes[2].lifecycle, ProcessLifecycle.EXITED)
        self.assertEqual(changes[1].cpu_time.state, ComparisonState.IDENTITY_MISMATCH)

    def test_partial_current_collection_does_not_create_false_exits(self) -> None:
        prior = collection((process(1, 10, 1), process(2, 20, 1)))
        current = collection((process(1, 10, 2),), CollectionStatus.PARTIAL)
        changes = compare_process_collections(prior, current, 1).changes
        self.assertEqual([item.lifecycle for item in changes], [ProcessLifecycle.CONTINUING])
        self.assertFalse(compare_process_collections(prior, current, 1).current_collection_complete)

    def test_failed_process_collection_makes_no_lifecycle_claims(self) -> None:
        prior = collection((process(1, 10, 1),))
        failed = collection((), CollectionStatus.TRANSIENT_FAILURE)
        result = compare_process_collections(prior, failed, 1)
        self.assertEqual(result.changes, ())
        self.assertFalse(result.current_collection_complete)

    def test_missing_or_duplicate_identity_is_invalid(self) -> None:
        invalid = compare_process_collections(collection((process(1, 10, 1),)), collection((process(1, None, 2),)), 1)
        self.assertEqual(invalid.changes[0].lifecycle, ProcessLifecycle.INVALID)
        duplicate = collection((process(1, 10, 1), process(1, 10, 2)))
        with self.assertRaises(ValueError):
            compare_process_collections(duplicate, collection((process(1, 10, 3),)), 1)

    def test_counter_reset_and_invalid_elapsed_never_fake_cpu(self) -> None:
        prior = collection((process(1, 10, 10),))
        reset = compare_process_collections(prior, collection((process(1, 10, 5),)), 1).changes[0]
        self.assertEqual(reset.cpu_time.state, ComparisonState.COUNTER_RESET)
        invalid_time = compare_process_collections(prior, collection((process(1, 10, 15),)), 0).changes[0]
        self.assertEqual(invalid_time.cpu_time.state, ComparisonState.INVALID_INTERVAL)

    def test_large_unordered_collections_are_matched_by_identity(self) -> None:
        previous = tuple(process(pid, pid * 10, pid) for pid in range(400))
        current = tuple(reversed(tuple(process(pid, pid * 10, pid + 2) for pid in range(400))))
        changes = compare_process_collections(collection(previous), collection(current), 2).changes
        self.assertEqual(len(changes), 400)
        self.assertTrue(all(change.lifecycle is ProcessLifecycle.CONTINUING for change in changes))
