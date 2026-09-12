"""Service lifecycle facts derived from existing service observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import CollectionResult, CollectionStatus, ServiceObservation


class ServiceLifecycle(StrEnum):
    INSUFFICIENT_HISTORY = "insufficient_history"
    NEW = "new"
    CONTINUING = "continuing"
    STATE_CHANGED = "state_changed"
    REMOVED = "removed"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ServiceChange:
    lifecycle: ServiceLifecycle
    name: str
    previous: ServiceObservation | None
    current: ServiceObservation | None


@dataclass(frozen=True, slots=True)
class ServiceIntelligence:
    previous_collection_complete: bool
    current_collection_complete: bool
    changes: tuple[ServiceChange, ...]


def compare_service_collections(previous: SnapshotCollection | None, current: SnapshotCollection) -> ServiceIntelligence:
    """Compare canonical service names in O(N); partial lists never imply removals."""
    current_services, current_complete = _services(current)
    current_by_name = _by_name(current_services)
    current_complete = current_complete and all(_valid(item) for item in current_services)
    if previous is None:
        return ServiceIntelligence(False, current_complete, tuple(ServiceChange(
                                   ServiceLifecycle.INSUFFICIENT_HISTORY if _valid(item) else ServiceLifecycle.INVALID,
                                   item.name, None, item) for item in current_services))
    previous_services, previous_complete = _services(previous)
    previous_by_name = _by_name(previous_services)
    previous_complete = previous_complete and all(_valid(item) for item in previous_services)
    changes: list[ServiceChange] = []
    for item in current_services:
        prior = previous_by_name.get(item.name)
        if not _valid(item) or (prior is not None and not _valid(prior)):
            lifecycle = ServiceLifecycle.INVALID
        elif prior is None:
            lifecycle = (ServiceLifecycle.NEW if previous_complete and current_complete
                         else ServiceLifecycle.INSUFFICIENT_HISTORY)
        elif (prior.load_state, prior.active_state, prior.sub_state) == (item.load_state, item.active_state, item.sub_state):
            lifecycle = ServiceLifecycle.CONTINUING
        else:
            lifecycle = ServiceLifecycle.STATE_CHANGED
        changes.append(ServiceChange(lifecycle, item.name, prior, item))
    if previous_complete and current_complete:
        changes.extend(ServiceChange(ServiceLifecycle.REMOVED, item.name, item, None)
                       for item in previous_services if item.name not in current_by_name)
    return ServiceIntelligence(previous_complete, current_complete, tuple(changes))


def _services(collection: SnapshotCollection) -> tuple[tuple[ServiceObservation, ...], bool]:
    result = _service_result(collection)
    if result is None or result.value is None or result.status not in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL):
        return (), False
    return collection.snapshot.services, result.status is CollectionStatus.SUCCESS


def _service_result(collection: SnapshotCollection) -> CollectionResult[object] | None:
    matches = [result for name, result in collection.collector_results if name == "services"]
    if len(matches) > 1:
        raise ValueError("collection contains duplicate service results")
    return matches[0] if matches else None


def _by_name(services: tuple[ServiceObservation, ...]) -> dict[str, ServiceObservation]:
    output: dict[str, ServiceObservation] = {}
    for item in services:
        if item.name in output:
            raise ValueError("service collection contains duplicate names")
        output[item.name] = item
    return output


def _valid(item: ServiceObservation) -> bool:
    """Validate shape without assuming a closed set of future systemd states."""
    return (item.name.endswith(".service") and len(item.name) > len(".service")
            and all(value and not any(character.isspace() or ord(character) < 32
                                     for character in value)
                    for value in (item.name, item.load_state, item.active_state, item.sub_state)))
