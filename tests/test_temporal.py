from datetime import UTC, datetime
import math
import unittest

from sentinel.analysis.temporal import ComparisonState, compare_counter, compare_cpu, compare_snapshots
from sentinel.models import CPUObservation, NetworkObservation, ProcessObservation, SystemSnapshot


def cpu(*values: int) -> CPUObservation:
    return CPUObservation(*(values or (10, 2, 3, 80, 1, 1, 1, 0)))


def network(name: str, received: int, transmitted: int) -> NetworkObservation:
    return NetworkObservation(name, received, transmitted, None, None, None, None, None, None)


def process(pid: int, started: int | None, ticks: int | None) -> ProcessObservation:
    return ProcessObservation(pid, 1, "worker", "S", 1, None, 1, ticks, started, None, None)


def snapshot(*, cpu_value: CPUObservation | None = None,
             networks: tuple[NetworkObservation, ...] = (),
             processes: tuple[ProcessObservation, ...] = ()) -> SystemSnapshot:
    return SystemSnapshot(datetime(2026, 1, 1, tzinfo=UTC), None, None, cpu_value, processes, (), networks, (), (), ())


class CounterTests(unittest.TestCase):
    def test_counter_uses_actual_elapsed_and_never_makes_unknown_zero(self) -> None:
        self.assertEqual(compare_counter(None, 160, 2.5).state, ComparisonState.NO_PREVIOUS)
        valid = compare_counter(100, 160, 2.5)
        self.assertEqual((valid.state, valid.delta, valid.rate_per_second), (ComparisonState.VALID, 60, 24.0))
        elapsed = compare_counter(0, 527, 5.27)
        self.assertAlmostEqual(elapsed.rate_per_second or 0, 100.0)

    def test_counter_invalid_time_reset_and_missing_are_not_rates(self) -> None:
        for elapsed in (0, -1, math.nan, math.inf, True, None):
            with self.subTest(elapsed=elapsed):
                result = compare_counter(10, 20, elapsed)
                self.assertEqual(result.state, ComparisonState.INVALID_INTERVAL)
                self.assertIsNone(result.rate_per_second)
        reset = compare_counter(20, 10, 1)
        self.assertEqual(reset.state, ComparisonState.COUNTER_RESET)
        self.assertIsNone(reset.delta)
        self.assertEqual(compare_counter(1, None, 1).state, ComparisonState.INVALID_DATA)


class TemporalSnapshotTests(unittest.TestCase):
    def test_cpu_uses_raw_counter_deltas_and_actual_elapsed(self) -> None:
        previous = cpu(10, 2, 3, 80, 1, 1, 1, 0)
        current = cpu(15, 2, 8, 90, 1, 1, 1, 0)
        result = compare_cpu(previous, current, 5.0)
        self.assertEqual(result.state, ComparisonState.VALID)
        self.assertEqual((result.total_ticks_delta, result.busy_ticks_delta), (20, 10))
        self.assertEqual((result.total_ticks_rate_per_second, result.busy_ticks_rate_per_second), (4.0, 2.0))
        self.assertEqual(result.utilization, 0.5)
        self.assertEqual(compare_cpu(current, previous, 5).state, ComparisonState.COUNTER_RESET)
        self.assertEqual(compare_cpu(previous, previous, 5).state, ComparisonState.INVALID_DATA)

    def test_networks_match_by_interface_and_show_new_removed_reset(self) -> None:
        previous = snapshot(networks=(network("eth0", 100, 200), network("old0", 1, 1)))
        current = snapshot(networks=(network("wlan0", 5, 5), network("eth0", 160, 150)))
        result = compare_snapshots(previous, current, 2.5)
        by_name = {item.interface: item for item in result.network}
        self.assertEqual(by_name["wlan0"].receive.state, ComparisonState.NEW_RESOURCE)
        self.assertEqual(by_name["eth0"].receive.rate_per_second, 24.0)
        self.assertEqual(by_name["eth0"].transmit.state, ComparisonState.COUNTER_RESET)
        self.assertEqual(result.removed_network_interfaces, ("old0",))

    def test_process_lifetime_identity_prevents_pid_reuse_comparison(self) -> None:
        previous = snapshot(processes=(process(42, 100, 30), process(77, 5, 1)))
        current = snapshot(processes=(process(42, 100, 45), process(77, 6, 10), process(99, None, 5)))
        result = compare_snapshots(previous, current, 3.0)
        by_lifetime = {item.lifetime_id: item for item in result.processes}
        self.assertEqual(by_lifetime["42:100"].cpu_time.rate_per_second, 5.0)
        self.assertEqual(by_lifetime["77:6"].cpu_time.state, ComparisonState.IDENTITY_MISMATCH)
        self.assertEqual(by_lifetime[None].cpu_time.state, ComparisonState.IDENTITY_MISMATCH)
        self.assertEqual(result.removed_process_lifetimes, ("77:5",))

    def test_first_snapshot_has_unknown_not_zero_rates(self) -> None:
        current = snapshot(cpu_value=cpu(), networks=(network("eth0", 1, 2),), processes=(process(1, 3, 4),))
        result = compare_snapshots(None, current, 1.0)
        self.assertEqual(result.cpu.state, ComparisonState.NO_PREVIOUS)
        self.assertEqual(result.network[0].receive.state, ComparisonState.NO_PREVIOUS)
        self.assertEqual(result.processes[0].cpu_time.state, ComparisonState.NO_PREVIOUS)
