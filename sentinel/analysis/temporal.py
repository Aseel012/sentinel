"""Small, pure temporal comparisons over existing raw Sentinel observations.

Callers provide actual elapsed monotonic time.  Persisted wall timestamps remain
useful for history display but must not be silently used for rate calculations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from sentinel.models import CPUObservation, NetworkObservation, ProcessObservation, SystemSnapshot


class ComparisonState(StrEnum):
    NO_PREVIOUS = "no_previous"
    VALID = "valid"
    COUNTER_RESET = "counter_reset"
    INVALID_INTERVAL = "invalid_interval"
    NEW_RESOURCE = "new_resource"
    REMOVED_RESOURCE = "removed_resource"
    IDENTITY_MISMATCH = "identity_mismatch"
    INVALID_DATA = "invalid_data"


@dataclass(frozen=True, slots=True)
class CounterComparison:
    state: ComparisonState
    delta: int | None = None
    rate_per_second: float | None = None


@dataclass(frozen=True, slots=True)
class CPUComparison:
    state: ComparisonState
    total_ticks_delta: int | None = None
    busy_ticks_delta: int | None = None
    total_ticks_rate_per_second: float | None = None
    busy_ticks_rate_per_second: float | None = None
    utilization: float | None = None


@dataclass(frozen=True, slots=True)
class NetworkComparison:
    interface: str
    receive: CounterComparison
    transmit: CounterComparison


@dataclass(frozen=True, slots=True)
class ProcessComparison:
    lifetime_id: str | None
    cpu_time: CounterComparison


@dataclass(frozen=True, slots=True)
class TemporalComparison:
    elapsed_seconds: float | None
    cpu: CPUComparison
    network: tuple[NetworkComparison, ...]
    removed_network_interfaces: tuple[str, ...]
    processes: tuple[ProcessComparison, ...]
    removed_process_lifetimes: tuple[str, ...]


def compare_counter(previous: int | None, current: int | None, elapsed_seconds: float | None) -> CounterComparison:
    """Compare one cumulative counter without ever converting unknown into zero."""
    if previous is None:
        return CounterComparison(ComparisonState.NO_PREVIOUS)
    if current is None:
        return CounterComparison(ComparisonState.INVALID_DATA)
    if not _valid_elapsed(elapsed_seconds):
        return CounterComparison(ComparisonState.INVALID_INTERVAL)
    if previous < 0 or current < 0:
        return CounterComparison(ComparisonState.INVALID_DATA)
    if current < previous:
        return CounterComparison(ComparisonState.COUNTER_RESET)
    delta = current - previous
    return CounterComparison(ComparisonState.VALID, delta, delta / elapsed_seconds)


def compare_cpu(previous: CPUObservation | None, current: CPUObservation | None, elapsed_seconds: float | None) -> CPUComparison:
    """Derive utilization from raw Linux aggregate CPU tick counters."""
    if previous is None:
        return CPUComparison(ComparisonState.NO_PREVIOUS)
    if current is None:
        return CPUComparison(ComparisonState.INVALID_DATA)
    if not _valid_elapsed(elapsed_seconds):
        return CPUComparison(ComparisonState.INVALID_INTERVAL)
    prior_values = _cpu_values(previous)
    current_values = _cpu_values(current)
    if any(value < 0 for value in prior_values + current_values):
        return CPUComparison(ComparisonState.INVALID_DATA)
    if any(after < before for before, after in zip(prior_values, current_values, strict=True)):
        return CPUComparison(ComparisonState.COUNTER_RESET)
    total_delta = current.total_ticks - previous.total_ticks
    idle_delta = (current.idle_ticks + current.iowait_ticks) - (previous.idle_ticks + previous.iowait_ticks)
    busy_delta = total_delta - idle_delta
    if total_delta <= 0 or busy_delta < 0 or busy_delta > total_delta:
        return CPUComparison(ComparisonState.INVALID_DATA)
    return CPUComparison(ComparisonState.VALID, total_delta, busy_delta, total_delta / elapsed_seconds,
                         busy_delta / elapsed_seconds, busy_delta / total_delta)


def compare_snapshots(previous: SystemSnapshot | None, current: SystemSnapshot, elapsed_seconds: float | None) -> TemporalComparison:
    """Compare existing snapshots in O(N), matching network/process identities safely."""
    if previous is None:
        return TemporalComparison(elapsed_seconds, compare_cpu(None, current.cpu, elapsed_seconds),
                                  tuple(NetworkComparison(item.interface,
                                      CounterComparison(ComparisonState.NO_PREVIOUS),
                                      CounterComparison(ComparisonState.NO_PREVIOUS)) for item in current.network),
                                  (), tuple(ProcessComparison(item.lifetime_id,
                                      CounterComparison(ComparisonState.NO_PREVIOUS)) for item in current.processes), ())
    prior_network = {item.interface: item for item in previous.network}
    current_names = {item.interface for item in current.network}
    networks = tuple(_compare_network(prior_network.get(item.interface), item, elapsed_seconds) for item in current.network)
    prior_processes = {item.lifetime_id: item for item in previous.processes if item.lifetime_id is not None}
    prior_pids = {item.pid for item in previous.processes}
    current_lifetimes = {item.lifetime_id for item in current.processes if item.lifetime_id is not None}
    processes = tuple(_compare_process(prior_processes.get(item.lifetime_id), item, elapsed_seconds,
                                       item.pid in prior_pids) for item in current.processes)
    return TemporalComparison(elapsed_seconds, compare_cpu(previous.cpu, current.cpu, elapsed_seconds), networks,
                              tuple(sorted(set(prior_network) - current_names)), processes,
                              tuple(sorted(item for item in set(prior_processes) - current_lifetimes if item is not None)))


def _compare_network(previous: NetworkObservation | None, current: NetworkObservation, elapsed: float | None) -> NetworkComparison:
    state = ComparisonState.NEW_RESOURCE
    if previous is None:
        return NetworkComparison(current.interface, CounterComparison(state), CounterComparison(state))
    return NetworkComparison(current.interface, compare_counter(previous.receive_bytes, current.receive_bytes, elapsed),
                             compare_counter(previous.transmit_bytes, current.transmit_bytes, elapsed))


def _compare_process(previous: ProcessObservation | None, current: ProcessObservation, elapsed: float | None,
                     pid_seen_before: bool) -> ProcessComparison:
    if current.lifetime_id is None:
        return ProcessComparison(None, CounterComparison(ComparisonState.IDENTITY_MISMATCH))
    if previous is None:
        state = ComparisonState.IDENTITY_MISMATCH if pid_seen_before else ComparisonState.NEW_RESOURCE
        return ProcessComparison(current.lifetime_id, CounterComparison(state))
    return ProcessComparison(current.lifetime_id, compare_counter(previous.cpu_time_ticks, current.cpu_time_ticks, elapsed))


def _valid_elapsed(value: float | None) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _cpu_values(observation: CPUObservation) -> tuple[int, ...]:
    return (observation.user_ticks, observation.nice_ticks, observation.system_ticks, observation.idle_ticks,
            observation.iowait_ticks, observation.irq_ticks, observation.softirq_ticks, observation.steal_ticks)
