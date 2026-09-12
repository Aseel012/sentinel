from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sentinel.application.event_query_service import CorrelationEvents
from sentinel.application.event_service import JournalEventService
from sentinel.application.incident_service import IncidentFormation, IncidentFormationService
from sentinel.application.persistence_service import PersistentSnapshotService
from sentinel.application.runtime import ContinuousObservationRuntime
from sentinel.application.runtime_models import RuntimeConfig
from sentinel.cli.runtime_output import runtime_cycle_response
from sentinel.models import (
    CollectionResult, CollectionStatus, EventObservation, IncidentState, JournalBatch,
    ServiceObservation,
)
from sentinel.storage.database import database_connection
from sentinel.storage.events import journal_cursor
from sentinel.storage.incidents import IncidentReconciliation, IncidentRepository
from sentinel.storage.snapshots import StoredSnapshot
from tests.test_snapshot_repository import collection


NOW = datetime(2026, 7, 1, tzinfo=UTC)


class IncrementingClock:
    def __init__(self, step: float = 0.1) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class NoopRuntimeLease:
    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return None


def collection_at(at: datetime, active_state: str = "active", *,
                  process_status: CollectionStatus = CollectionStatus.SUCCESS):
    original = collection()
    service = ServiceObservation("worker.service", "loaded", active_state,
                                 "running" if active_state == "active" else active_state)
    results = []
    for name, result in original.collector_results:
        value = result.value
        status = result.status
        warnings = result.warnings
        code = result.error_code
        if name == "services":
            value = (service,)
        if name == "processes" and process_status is not CollectionStatus.SUCCESS:
            status = process_status
            value = (
                original.snapshot.processes
                if process_status is CollectionStatus.PARTIAL
                else None
            )
            warnings = ("process inventory incomplete",)
            code = "processes_incomplete"
        results.append((name, replace(result, value=value, status=status, collected_at=at,
                                      warnings=warnings, error_code=code)))
    snapshot = replace(
        original.snapshot,
        timestamp=at,
        services=(service,),
        results=tuple((name, result.status.value) for name, result in results),
        warnings=tuple(warning for _, result in results for warning in result.warnings),
    )
    return replace(original, snapshot=snapshot, collector_results=tuple(results))


class SequenceSnapshotRecorder:
    def __init__(self, *collections) -> None:
        self.values = list(collections)
        self.calls = 0

    def record(self) -> StoredSnapshot:
        value = self.values[self.calls]
        self.calls += 1
        return StoredSnapshot(self.calls, value)


class SequenceEventRecorder:
    def __init__(self, *results) -> None:
        self.values = list(results)
        self.calls = 0

    def record(self):
        value = self.values[self.calls]
        self.calls += 1
        return value


class ConstantSnapshotRecorder:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def record(self) -> StoredSnapshot:
        self.calls += 1
        return StoredSnapshot(self.calls, self.value)


class ConstantRecorder:
    def __init__(self, value) -> None:
        self.value = value
        self.calls = 0

    def record(self):
        self.calls += 1
        return self.value


class RaisingRecorder:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def record(self):
        raise self.error


class RaisingEventReader:
    def load_for_service_changes(self, changes, *, correlation_window, limit):
        raise sqlite3.OperationalError("event query failed")


class EmptyEventReader:
    def __init__(self) -> None:
        self.calls = []

    def load_for_service_changes(self, changes, *, correlation_window, limit):
        self.calls.append((changes, correlation_window, limit))
        return CorrelationEvents((), False)


class CapturingIncidentRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = []
        self.error = error

    def record(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return IncidentFormation((), IncidentReconciliation((), ()))


def journal_result(status: CollectionStatus = CollectionStatus.SUCCESS):
    value = JournalBatch((), None) if status in (CollectionStatus.SUCCESS,
                                                 CollectionStatus.PARTIAL) else None
    return CollectionResult(value, status, NOW, 0, None if value is not None else "unavailable")


class RuntimeTests(unittest.TestCase):
    def runtime(self, snapshots, journals, *, incident=None, reader=None, sink=None, clock=None):
        return ContinuousObservationRuntime(
            config=RuntimeConfig(interval_seconds=1),
            snapshot_recorder=SequenceSnapshotRecorder(*snapshots),
            event_recorder=SequenceEventRecorder(*journals),
            event_reader=reader or EmptyEventReader(),
            incident_recorder=incident or CapturingIncidentRecorder(),
            monotonic=clock or IncrementingClock(),
            wait=lambda _: False,
            cycle_sink=sink,
            runtime_lease=NoopRuntimeLease(),
        )

    def test_successful_cycle_is_structured_deterministic_and_private(self) -> None:
        recorder = CapturingIncidentRecorder()
        reader = EmptyEventReader()
        runtime = self.runtime((collection_at(NOW),), (journal_result(),),
                               incident=recorder, reader=reader)
        result = runtime.record()
        self.assertEqual(result.cycle_number, 1)
        self.assertEqual(result.snapshot_id, 1)
        self.assertEqual(dict(result.observation_counts)["processes"], 1)
        self.assertEqual(result.events_processed, 0)
        self.assertEqual(result.collection_quality[-1][0], "services")
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(len(reader.calls), 1)
        encoded = json.dumps(runtime_cycle_response(result), sort_keys=True)
        self.assertNotIn("worker --token", encoded)
        self.assertNotIn("/usr/bin/worker", encoded)
        self.assertNotIn("message", encoded)
        baseline = runtime._previous_collection
        self.assertIsNotNone(baseline)
        self.assertEqual(
            tuple(name for name, _ in baseline.collector_results),
            ("processes", "services"),
        )
        self.assertTrue(
            all(
                item.command is None and item.executable is None
                for item in baseline.snapshot.processes
            )
        )
        self.assertIsNone(baseline.snapshot.system)
        self.assertEqual(baseline.snapshot.disk, ())

    def test_partial_and_failed_sources_remain_visible_without_fake_facts(self) -> None:
        current = collection_at(NOW, process_status=CollectionStatus.TRANSIENT_FAILURE)
        incident = CapturingIncidentRecorder()
        result = self.runtime((current,), (journal_result(CollectionStatus.PERMISSION_DENIED),),
                              incident=incident).record()
        self.assertIn("collector_processes_transient_failure", result.limitations)
        self.assertIn("journal_collection_permission_denied", result.limitations)
        process_changes = incident.calls[0][0][1]
        self.assertEqual(process_changes, ())
        self.assertEqual(incident.calls[0][0][2], ())

    def test_multiple_finite_cycles_do_not_accumulate_results(self) -> None:
        values = tuple(collection_at(NOW + timedelta(seconds=index)) for index in range(3))
        emitted = []
        runtime = self.runtime(values, (journal_result(),) * 3, sink=emitted.append)
        run = runtime.run(max_cycles=3)
        self.assertEqual(run.samples_completed, 3)
        self.assertEqual([item.cycle_number for item in emitted], [1, 2, 3])
        self.assertNotIn("results", runtime.__dict__)
        with self.assertRaises(ValueError):
            runtime.run(max_cycles=0)

    def test_stop_finishes_current_cycle_and_prevents_another(self) -> None:
        emitted = []
        runtime = None

        def stop_after(cycle):
            emitted.append(cycle)
            runtime.request_stop()

        runtime = self.runtime((collection_at(NOW), collection_at(NOW)),
                               (journal_result(), journal_result()), sink=stop_after)
        result = runtime.run(max_cycles=2)
        self.assertEqual(result.samples_completed, 1)
        self.assertTrue(result.stopped)
        self.assertEqual(len(emitted), 1)

    def test_storage_and_invariant_failures_surface_without_completed_result(self) -> None:
        for error in (sqlite3.OperationalError("disk full"), ValueError("broken invariant")):
            emitted = []
            incident = CapturingIncidentRecorder(error)
            runtime = self.runtime((collection_at(NOW),), (journal_result(),),
                                   incident=incident, sink=emitted.append)
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                runtime.record()
            self.assertEqual(emitted, [])

    def test_each_failed_cycle_stage_preserves_the_prior_baseline(self) -> None:
        failures = (
            {"snapshot_recorder": RaisingRecorder(sqlite3.OperationalError("snapshot"))},
            {"event_recorder": RaisingRecorder(sqlite3.OperationalError("journal"))},
            {"event_reader": RaisingEventReader()},
            {"incident_recorder": CapturingIncidentRecorder(
                sqlite3.OperationalError("incident")
            )},
        )
        for replacement in failures:
            runtime = ContinuousObservationRuntime(
                config=RuntimeConfig(interval_seconds=1),
                snapshot_recorder=replacement.get(
                    "snapshot_recorder",
                    SequenceSnapshotRecorder(collection_at(NOW)),
                ),
                event_recorder=replacement.get(
                    "event_recorder",
                    SequenceEventRecorder(journal_result()),
                ),
                event_reader=replacement.get("event_reader", EmptyEventReader()),
                incident_recorder=replacement.get(
                    "incident_recorder",
                    CapturingIncidentRecorder(),
                ),
                monotonic=IncrementingClock(),
                runtime_lease=NoopRuntimeLease(),
            )
            with self.subTest(stage=tuple(replacement)), \
                    self.assertRaises(sqlite3.OperationalError):
                runtime.record()
            self.assertIsNone(runtime._previous_collection)
            self.assertIsNone(runtime._previous_observed_monotonic)
            self.assertEqual(runtime._cycles_completed, 0)

    def test_long_finite_run_keeps_only_one_sanitized_baseline(self) -> None:
        cycles = 500
        snapshot = ConstantSnapshotRecorder(collection_at(NOW))
        runtime = ContinuousObservationRuntime(
            config=RuntimeConfig(interval_seconds=1),
            snapshot_recorder=snapshot,
            event_recorder=ConstantRecorder(journal_result()),
            event_reader=EmptyEventReader(),
            incident_recorder=CapturingIncidentRecorder(),
            monotonic=IncrementingClock(),
            wait=lambda _: False,
            runtime_lease=NoopRuntimeLease(),
        )
        result = runtime.run(max_cycles=cycles)
        self.assertEqual(result.samples_completed, cycles)
        self.assertEqual(snapshot.calls, cycles)
        self.assertEqual(runtime._cycles_completed, cycles)
        self.assertEqual(len(runtime._previous_collection.collector_results), 2)
        self.assertNotIn("results", runtime.__dict__)

    def test_regressing_or_nonfinite_monotonic_clock_is_rejected(self) -> None:
        for values in ((1.0, 2.0, 0.0), (1.0, float("nan"))):
            iterator = iter(values)
            runtime = self.runtime((collection_at(NOW),), (journal_result(),),
                                   clock=lambda: next(iterator))
            with self.subTest(values=values), self.assertRaises(ValueError):
                runtime.record()

    def test_configuration_bounds_are_enforced_before_work(self) -> None:
        for value in (0.01, 86_401, float("nan"), True, "1"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                RuntimeConfig(interval_seconds=value)


class RuntimeIntegrationTests(unittest.TestCase):
    def test_real_services_persist_open_and_resolve_without_replaying_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            snapshots = [
                collection_at(NOW, "active"),
                collection_at(NOW + timedelta(seconds=10), "failed"),
                collection_at(NOW + timedelta(seconds=20), "active"),
            ]

            class SnapshotSource:
                def collect(self):
                    return snapshots.pop(0)

            seen_cursors = []

            def collect_events(cursor):
                seen_cursors.append(cursor)
                if len(seen_cursors) == 1:
                    event = EventObservation("cursor-1", NOW + timedelta(seconds=5),
                                             "journal", 3, "worker.service", None, None,
                                             "raw private body", "boot")
                    return CollectionResult(JournalBatch((event,), event.cursor),
                                            CollectionStatus.SUCCESS, event.timestamp, 0)
                return CollectionResult(JournalBatch((), cursor), CollectionStatus.SUCCESS,
                                        NOW, 0)

            runtime = ContinuousObservationRuntime(
                path,
                RuntimeConfig(interval_seconds=1, max_snapshots=2),
                snapshot_recorder=PersistentSnapshotService(
                    path, SnapshotSource(), max_snapshots=2,
                ),
                event_recorder=JournalEventService(path, collect_events),
                incident_recorder=IncidentFormationService(path),
                monotonic=IncrementingClock(),
                wait=lambda _: False,
            )
            first = runtime.record()
            second = runtime.record()
            third = runtime.record()
            self.assertEqual(first.incident_candidate_ids, ())
            self.assertEqual(second.events_processed, 0)
            self.assertEqual(second.events_correlated, 1)
            self.assertEqual(len(second.reconciled_candidate_ids), 1)
            self.assertEqual(third.resolved_incident_ids, second.reconciled_candidate_ids)
            with database_connection(path) as connection:
                snapshot_count = connection.execute(
                    "SELECT COUNT(*) FROM snapshots"
                ).fetchone()[0]
                self.assertEqual(snapshot_count, 2)
                self.assertEqual(journal_cursor(connection), "cursor-1")
                incidents = IncidentRepository(connection).list_recent()
                self.assertEqual(len(incidents), 1)
                self.assertEqual(incidents[0].state, IncidentState.RESOLVED)
                self.assertNotIn("raw private body",
                                 " ".join(item.summary or "" for item in incidents[0].evidence))
            self.assertEqual(seen_cursors, [None, "cursor-1", "cursor-1"])

    def test_restart_is_conservative_idempotent_and_preserves_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            event = EventObservation(
                "restart-cursor",
                NOW + timedelta(seconds=5),
                "journal",
                3,
                "worker.service",
                None,
                None,
                "private event body",
                "boot",
            )
            seen_cursors = []

            def collect_events(cursor):
                seen_cursors.append(cursor)
                batch = (
                    JournalBatch((event,), event.cursor)
                    if cursor is None
                    else JournalBatch((), cursor)
                )
                return CollectionResult(
                    batch,
                    CollectionStatus.SUCCESS,
                    event.timestamp,
                    0,
                )

            class SnapshotSource:
                def __init__(self, values):
                    self.values = list(values)

                def collect(self):
                    return self.values.pop(0)

            def runtime_for(values):
                source = SnapshotSource(values)
                return ContinuousObservationRuntime(
                    path,
                    RuntimeConfig(interval_seconds=1),
                    snapshot_recorder=PersistentSnapshotService(path, source),
                    event_recorder=JournalEventService(path, collect_events),
                    incident_recorder=IncidentFormationService(path),
                    monotonic=IncrementingClock(),
                )

            before_restart = runtime_for((
                collection_at(NOW, "active"),
                collection_at(NOW + timedelta(seconds=10), "failed"),
                collection_at(NOW + timedelta(seconds=20), "failed"),
            ))
            before_restart.record()
            opened = before_restart.record()
            repeated = before_restart.record()
            self.assertEqual(len(opened.reconciled_candidate_ids), 1)
            self.assertEqual(repeated.reconciled_candidate_ids, ())

            after_restart = runtime_for((
                collection_at(NOW + timedelta(seconds=30), "active"),
            ))
            conservative = after_restart.record()
            self.assertEqual(conservative.resolved_incident_ids, ())
            with database_connection(path) as connection:
                self.assertEqual(journal_cursor(connection), event.cursor)
                incidents = IncidentRepository(connection).list_recent()
                self.assertEqual(len(incidents), 1)
                self.assertEqual(incidents[0].state, IncidentState.ACTIVE)
            self.assertEqual(
                seen_cursors,
                [None, event.cursor, event.cursor, event.cursor],
            )
