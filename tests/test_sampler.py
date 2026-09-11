import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sentinel.application.persistence_service import PersistentSnapshotService
from sentinel.application.sampler import Sampler, SamplingConfig
from sentinel.storage.database import database_connection
from tests.test_snapshot_repository import collection


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    def __init__(self, clock: FakeClock, work_seconds: float = 0.0) -> None:
        self.clock = clock
        self.work_seconds = work_seconds
        self.started: list[float] = []
        self.active = False
        self.maximum_active = 0

    def record(self) -> None:
        if self.active:
            raise AssertionError("overlapping sample")
        self.active = True
        self.maximum_active = max(self.maximum_active, 1)
        self.started.append(self.clock())
        self.clock.advance(self.work_seconds)
        self.active = False


class SamplerTests(unittest.TestCase):
    def waiter(self, clock: FakeClock, delays: list[float]):
        def wait(delay: float) -> bool:
            delays.append(delay)
            clock.advance(delay)
            return False
        return wait

    def test_immediate_samples_follow_monotonic_deadlines_without_drift(self) -> None:
        clock = FakeClock()
        recorder = Recorder(clock, work_seconds=0.2)
        delays: list[float] = []
        run = Sampler(recorder, SamplingConfig(1.0), monotonic=clock, wait=self.waiter(clock, delays)).run(max_samples=3)
        self.assertEqual(recorder.started, [0.0, 1.0, 2.0])
        self.assertEqual(len(delays), 2)
        self.assertTrue(all(abs(delay - 0.8) < 1e-9 for delay in delays))
        self.assertEqual(run.samples_completed, 3)
        self.assertFalse(run.stopped)
        self.assertEqual(recorder.maximum_active, 1)

    def test_slow_sample_skips_missed_intervals_without_backlog(self) -> None:
        clock = FakeClock()
        recorder = Recorder(clock, work_seconds=3.2)
        delays: list[float] = []
        Sampler(recorder, SamplingConfig(1.0), monotonic=clock, wait=self.waiter(clock, delays)).run(max_samples=2)
        self.assertEqual(recorder.started, [0.0, 4.0])
        self.assertEqual(len(delays), 1)
        self.assertAlmostEqual(delays[0], 0.8)

    def test_stop_before_start_and_stop_after_current_sample(self) -> None:
        clock = FakeClock()
        recorder = Recorder(clock)
        sampler = Sampler(recorder, monotonic=clock, wait=self.waiter(clock, []))
        sampler.request_stop()
        self.assertEqual(sampler.run().samples_completed, 0)
        sampler.reset_stop_request()

        original_record = recorder.record
        def stop_after_record() -> None:
            original_record()
            sampler.request_stop()
        recorder.record = stop_after_record  # type: ignore[method-assign]
        run = sampler.run()
        self.assertEqual(run.samples_completed, 1)
        self.assertTrue(run.stopped)

    def test_stop_during_wait_ends_cleanly(self) -> None:
        clock = FakeClock()
        recorder = Recorder(clock)
        sampler: Sampler
        def wait(delay: float) -> bool:
            clock.advance(delay / 2)
            sampler.request_stop()
            return True
        sampler = Sampler(recorder, monotonic=clock, wait=wait)
        run = sampler.run()
        self.assertEqual(run.samples_completed, 1)
        self.assertTrue(run.stopped)

    def test_reentrant_run_is_rejected_and_zero_bound_does_no_work(self) -> None:
        clock = FakeClock()
        sampler: Sampler
        test_case = self
        class ReentrantRecorder:
            def __init__(self) -> None:
                self.calls = 0
            def record(self) -> None:
                self.calls += 1
                with test_case.assertRaises(RuntimeError):
                    sampler.run(max_samples=1)
        recorder = ReentrantRecorder()
        sampler = Sampler(recorder, monotonic=clock, wait=lambda _: False)
        self.assertEqual(sampler.run(max_samples=1).samples_completed, 1)
        self.assertEqual(recorder.calls, 1)
        self.assertEqual(sampler.run(max_samples=0).samples_completed, 0)

    def test_invalid_interval_and_bound_are_rejected(self) -> None:
        for value in (0, -1, True, math.nan, math.inf, "1"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    SamplingConfig(value)  # type: ignore[arg-type]
        self.assertEqual(SamplingConfig(0.25).interval_seconds, 0.25)
        with self.assertRaises(ValueError):
            Sampler(Recorder(FakeClock())).run(max_samples=-1)

    def test_persistence_and_unexpected_failures_propagate_without_retry(self) -> None:
        class FailingRecorder:
            def __init__(self, error: Exception) -> None:
                self.error = error
                self.calls = 0
            def record(self) -> None:
                self.calls += 1
                raise self.error
        for error in (sqlite3.OperationalError("disk full"), ValueError("programming error")):
            recorder = FailingRecorder(error)
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                Sampler(recorder, monotonic=FakeClock(), wait=lambda _: False).run()
            self.assertEqual(recorder.calls, 1)

    def test_sampler_uses_existing_persistence_service_without_real_time_or_linux(self) -> None:
        class StaticSnapshotService:
            def collect(self):
                return collection(partial_processes=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            service = PersistentSnapshotService(path, StaticSnapshotService())
            clock = FakeClock()
            Sampler(service, SamplingConfig(0.5), monotonic=clock, wait=self.waiter(clock, [])).run(max_samples=2)
            with database_connection(path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM collection_results WHERE status = 'partial'").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM collection_warnings").fetchone()[0], 2)
