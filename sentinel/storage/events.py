"""Bounded event persistence and journal checkpoint access."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from sentinel.models import CollectionResult, CollectionStatus, EventObservation, JournalBatch

from .database import transaction
from .retention import delete_events_before, trim_events

DEFAULT_MAX_EVENTS = 10_000
_UNCHECKED = object()


class JournalCheckpointConflict(RuntimeError):
    """Another collector advanced the checkpoint while this batch was collected."""


@dataclass(frozen=True, slots=True)
class EventWindow:
    """A bounded chronological event range and whether additional matches exist."""

    events: tuple[EventObservation, ...]
    truncated: bool


def journal_cursor(connection: sqlite3.Connection) -> str | None:
    row = connection.execute("SELECT cursor FROM event_checkpoints WHERE source = ?", ("journal",)).fetchone()
    return None if row is None else row[0]


def store_journal_batch(connection: sqlite3.Connection, batch: JournalBatch, *,
                        expected_cursor: str | None | object = _UNCHECKED,
                        max_events: int = DEFAULT_MAX_EVENTS) -> int:
    """Persist facts idempotently with a checkpoint and bounded retention."""
    with transaction(connection):
        _check_cursor(connection, expected_cursor)
        return _store_batch(connection, batch, max_events)


def store_journal_result(connection: sqlite3.Connection, result: CollectionResult[JournalBatch], *,
                         expected_cursor: str | None | object = _UNCHECKED,
                         max_events: int = DEFAULT_MAX_EVENTS) -> int:
    """Atomically persist accepted facts and latest quality, including failures."""
    with transaction(connection):
        _check_cursor(connection, expected_cursor)
        inserted = 0
        if result.value is not None:
            if result.status not in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL):
                raise ValueError("only successful or partial collections can persist event facts")
            inserted = _store_batch(connection, result.value, max_events)
        connection.execute("""INSERT INTO event_collection_results VALUES ('journal', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET status=excluded.status, collected_at=excluded.collected_at,
                duration_seconds=excluded.duration_seconds, event_count=excluded.event_count,
                error_code=excluded.error_code, error_message=excluded.error_message, warnings=excluded.warnings
            """, (result.status.value, _timestamp(result.collected_at), result.duration_seconds,
                  None if result.value is None else len(result.value.events), result.error_code,
                  result.error_message, json.dumps(result.warnings)))
    return inserted


def latest_journal_quality(connection: sqlite3.Connection) -> CollectionResult[int] | None:
    """Read latest quality; its value is accepted count, not retained count."""
    row = connection.execute("""SELECT event_count, status, collected_at, duration_seconds,
        error_code, error_message, warnings FROM event_collection_results WHERE source='journal'""").fetchone()
    if row is None:
        return None
    return CollectionResult(row[0], CollectionStatus(row[1]), datetime.fromisoformat(row[2]),
                            row[3], row[4], row[5], tuple(json.loads(row[6])))


def load_events(connection: sqlite3.Connection, *, limit: int = 200, unit: str | None = None,
                before: datetime | None = None) -> tuple[EventObservation, ...]:
    """Read newest facts in deterministic timestamp/cursor order, capped at 10,000."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= DEFAULT_MAX_EVENTS:
        raise ValueError("limit must be between 1 and 10000")
    predicates: list[str] = []
    values: list[object] = []
    if unit is not None:
        predicates.append("unit = ?")
        values.append(unit)
    if before is not None:
        predicates.append("observed_at < ?")
        values.append(_timestamp(before))
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    rows = connection.execute("SELECT cursor, observed_at, source, priority, unit, pid, comm, message, "
        "boot_id, warnings FROM events" + where + " ORDER BY observed_at DESC, cursor DESC LIMIT ?",
        (*values, limit))
    return tuple(EventObservation(row[0], datetime.fromisoformat(row[1]), *row[2:9],
                                  warnings=tuple(json.loads(row[9]))) for row in rows)


def load_event_window(
    connection: sqlite3.Connection,
    *,
    unit: str,
    start: datetime,
    end: datetime,
    limit: int = 200,
) -> EventWindow:
    """Read an inclusive indexed unit/time window without hiding a result cap."""
    if not isinstance(unit, str) or not unit or len(unit) > 512:
        raise ValueError("unit must contain between 1 and 512 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= DEFAULT_MAX_EVENTS:
        raise ValueError("limit must be between 1 and 10000")
    start_value = _timestamp(start)
    end_value = _timestamp(end)
    if end < start:
        raise ValueError("event window end cannot precede start")
    rows = tuple(connection.execute(
        """SELECT cursor, observed_at, source, priority, unit, pid, comm, message, boot_id, warnings
           FROM events WHERE unit = ? AND observed_at >= ? AND observed_at <= ?
           ORDER BY observed_at ASC, cursor ASC LIMIT ?""",
        (unit, start_value, end_value, limit + 1),
    ))
    events = tuple(
        EventObservation(row[0], datetime.fromisoformat(row[1]), *row[2:9],
                         warnings=tuple(json.loads(row[9])))
        for row in rows[:limit]
    )
    return EventWindow(events, len(rows) > limit)


def _check_cursor(connection: sqlite3.Connection, expected: object) -> None:
    if expected is not _UNCHECKED and journal_cursor(connection) != expected:
        raise JournalCheckpointConflict("journal checkpoint changed during collection; retry from stored cursor")


def _store_batch(connection: sqlite3.Connection, batch: JournalBatch, max_events: int) -> int:
    if batch.events:
        if batch.next_cursor != batch.events[-1].cursor:
            raise ValueError("checkpoint must match the last accepted event")
    elif batch.next_cursor != journal_cursor(connection):
        raise ValueError("an empty batch cannot advance or clear the checkpoint")
    if any(not event.cursor or not isinstance(event.cursor, str) for event in batch.events):
        raise ValueError("events require nonempty cursor identities")
    before = connection.total_changes
    connection.executemany("""INSERT INTO events(cursor, observed_at, source, priority, unit, pid,
        comm, message, boot_id, warnings) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cursor) DO NOTHING""", ((event.cursor, _timestamp(event.timestamp), event.source,
        event.priority, event.unit, event.pid, event.comm, event.message, event.boot_id,
        json.dumps(event.warnings)) for event in batch.events))
    inserted = connection.total_changes - before
    if batch.next_cursor is not None:
        connection.execute("""INSERT INTO event_checkpoints VALUES ('journal', ?, ?)
            ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at""",
            (batch.next_cursor, _timestamp(datetime.now(UTC))))
    trim_events(connection, max_events)
    return inserted


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("event timestamp must be timezone-aware UTC")
    return value.isoformat(timespec="microseconds")
