"""Bounded persisted journal evidence lookup for runtime correlation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from pathlib import Path

from sentinel.analysis import TimedServiceChange, is_incident_anchor
from sentinel.analysis.correlation import MAX_INPUTS
from sentinel.models import EventObservation
from sentinel.storage.database import database_connection
from sentinel.storage.events import DEFAULT_MAX_EVENTS, load_event_window
from sentinel.storage.migrations import initialize_schema

DEFAULT_RUNTIME_EVENT_LIMIT = 1_000


@dataclass(frozen=True, slots=True)
class CorrelationEvents:
    events: tuple[EventObservation, ...]
    truncated: bool


class EventQueryService:
    """Read exact-unit event windows without retaining journal content in runtime state."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = database_path

    def load_for_service_changes(
        self,
        changes: tuple[TimedServiceChange, ...],
        *,
        correlation_window: timedelta,
        limit: int = DEFAULT_RUNTIME_EVENT_LIMIT,
    ) -> CorrelationEvents:
        if (not isinstance(changes, tuple)
                or any(not isinstance(item, TimedServiceChange) for item in changes)):
            raise TypeError("changes must be a tuple of TimedServiceChange values")
        if len(changes) > MAX_INPUTS:
            raise ValueError(f"changes cannot exceed {MAX_INPUTS} values")
        if not isinstance(correlation_window, timedelta):
            raise TypeError("correlation_window must be a timedelta")
        seconds = correlation_window.total_seconds()
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("correlation_window must be finite and non-negative")
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or not 1 <= limit <= DEFAULT_MAX_EVENTS):
            raise ValueError(f"limit must be between 1 and {DEFAULT_MAX_EVENTS}")
        anchors = tuple(item for item in changes if is_incident_anchor(item.change))
        by_cursor: dict[str, EventObservation] = {}
        truncated = False
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            for item in anchors:
                remaining = limit - len(by_cursor)
                if remaining <= 0:
                    truncated = True
                    break
                window = load_event_window(
                    connection,
                    unit=item.change.name,
                    start=_window_bound(item.observed_at, correlation_window, subtract=True),
                    end=_window_bound(item.observed_at, correlation_window, subtract=False),
                    limit=remaining,
                )
                truncated = truncated or window.truncated
                for event in window.events:
                    previous = by_cursor.setdefault(event.cursor, event)
                    if previous != event:
                        raise ValueError("persisted journal cursor identifies conflicting events")
        events = tuple(
            sorted(by_cursor.values(), key=lambda event: (event.timestamp, event.cursor))
        )
        return CorrelationEvents(events, truncated)


def _window_bound(value: datetime, window: timedelta, *, subtract: bool) -> datetime:
    try:
        return value - window if subtract else value + window
    except OverflowError:
        return datetime.min.replace(tzinfo=UTC) if subtract else datetime.max.replace(tzinfo=UTC)
