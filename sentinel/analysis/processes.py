"""Conservative process lifecycle and resource comparisons between collections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import CollectionStatus, ProcessObservation

from .temporal import ComparisonState, CounterComparison, compare_counter


class ProcessLifecycle(StrEnum):
    INSUFFICIENT_HISTORY = "insufficient_history"
    NEW = "new"
    CONTINUING = "continuing"
    EXITED = "exited"
    IDENTITY_CHANGED = "identity_changed"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ProcessChange:
    """A process lifecycle fact plus safe, derived resource changes."""

    lifecycle: ProcessLifecycle
    lifetime_id: str | None
    pid: int
    previous: ProcessObservation | None
    current: ProcessObservation | None
    cpu_time: CounterComparison
    rss_bytes_delta: int | None
    virtual_memory_bytes_delta: int | None


@dataclass(frozen=True, slots=True)
class ProcessIntelligence:
    """Comparison output; false exits are avoided whenever process observation is partial."""

    elapsed_seconds: float | None
    previous_collection_complete: bool
    current_collection_complete: bool
    changes: tuple[ProcessChange, ...]


def compare_process_collections(
    previous: SnapshotCollection | None,
    current: SnapshotCollection,
    elapsed_seconds: float | None,
) -> ProcessIntelligence:
    """Classify process lifecycle using `pid:start_time_ticks` in O(N) time.

    A partial collection can prove that an observed process continues or is new, but
    it cannot prove that an unobserved process exited. Failed/unsupported process
    collection yields no lifecycle claims rather than fabricated exits.
    """
    current_processes, current_complete = _processes(current)
    if previous is None:
        return ProcessIntelligence(elapsed_seconds, False, current_complete,
                                   tuple(_insufficient(item) for item in current_processes))
    previous_processes, previous_complete = _processes(previous)
    if not current_processes and not _processes_available(current):
        return ProcessIntelligence(elapsed_seconds, previous_complete, False, ())
    previous_by_lifetime = _by_lifetime(previous_processes)
    current_by_lifetime = _by_lifetime(current_processes)
    previous_by_pid = {item.pid: item for item in previous_processes if item.lifetime_id is not None}
    changes: list[ProcessChange] = []
    for item in current_processes:
        lifetime_id = item.lifetime_id
        if lifetime_id is None:
            changes.append(_invalid(item))
            continue
        prior = previous_by_lifetime.get(lifetime_id)
        if prior is not None:
            changes.append(_continuing(prior, item, elapsed_seconds))
        elif item.pid in previous_by_pid:
            changes.append(_changed(item, previous_by_pid[item.pid]))
        elif previous_complete:
            changes.append(_new(item))
        else:
            changes.append(_insufficient(item))
    if previous_complete and current_complete:
        current_pids = {item.pid for item in current_processes}
        for item in previous_processes:
            if item.lifetime_id is not None and item.pid not in current_pids:
                changes.append(_exited(item))
    return ProcessIntelligence(elapsed_seconds, previous_complete, current_complete, tuple(changes))


def _processes(collection: SnapshotCollection) -> tuple[tuple[ProcessObservation, ...], bool]:
    result = _process_result(collection)
    if result is None or result.value is None or result.status not in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL):
        return (), False
    processes = collection.snapshot.processes
    return processes, result.status is CollectionStatus.SUCCESS


def _processes_available(collection: SnapshotCollection) -> bool:
    result = _process_result(collection)
    return result is not None and result.value is not None and result.status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL)


def _process_result(collection: SnapshotCollection):
    matches = [result for name, result in collection.collector_results if name == "processes"]
    if len(matches) > 1:
        raise ValueError("collection contains duplicate process results")
    return matches[0] if matches else None


def _by_lifetime(processes: tuple[ProcessObservation, ...]) -> dict[str, ProcessObservation]:
    output: dict[str, ProcessObservation] = {}
    for item in processes:
        lifetime_id = item.lifetime_id
        if lifetime_id is None:
            continue
        if lifetime_id in output:
            raise ValueError("process collection contains duplicate lifetime identities")
        output[lifetime_id] = item
    return output


def _continuing(previous: ProcessObservation, current: ProcessObservation, elapsed: float | None) -> ProcessChange:
    return ProcessChange(ProcessLifecycle.CONTINUING, current.lifetime_id, current.pid, previous, current,
                         compare_counter(previous.cpu_time_ticks, current.cpu_time_ticks, elapsed),
                         current.rss_bytes - previous.rss_bytes, _delta(previous.virtual_memory_bytes, current.virtual_memory_bytes))


def _new(current: ProcessObservation) -> ProcessChange:
    return ProcessChange(ProcessLifecycle.NEW, current.lifetime_id, current.pid, None, current,
                         CounterComparison(ComparisonState.NEW_RESOURCE), None, None)


def _changed(current: ProcessObservation, previous: ProcessObservation) -> ProcessChange:
    return ProcessChange(ProcessLifecycle.IDENTITY_CHANGED, current.lifetime_id, current.pid, previous, current,
                         CounterComparison(ComparisonState.IDENTITY_MISMATCH), None, None)


def _exited(previous: ProcessObservation) -> ProcessChange:
    return ProcessChange(ProcessLifecycle.EXITED, previous.lifetime_id, previous.pid, previous, None,
                         CounterComparison(ComparisonState.REMOVED_RESOURCE), None, None)


def _insufficient(current: ProcessObservation) -> ProcessChange:
    return ProcessChange(ProcessLifecycle.INSUFFICIENT_HISTORY, current.lifetime_id, current.pid, None, current,
                         CounterComparison(ComparisonState.NO_PREVIOUS), None, None)


def _invalid(current: ProcessObservation) -> ProcessChange:
    return ProcessChange(ProcessLifecycle.INVALID, None, current.pid, None, current,
                         CounterComparison(ComparisonState.IDENTITY_MISMATCH), None, None)


def _delta(previous: int | None, current: int | None) -> int | None:
    return None if previous is None or current is None else current - previous
