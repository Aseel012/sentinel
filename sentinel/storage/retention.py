"""Shared time-cutoff retention and transactional event-count bounds."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .database import transaction


def delete_events_before(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete event facts strictly before a UTC cutoff, preserving source cursors."""
    if cutoff.tzinfo is None or cutoff.utcoffset() != UTC.utcoffset(cutoff):
        raise ValueError("event retention cutoff must be timezone-aware UTC")
    with transaction(connection):
        cursor = connection.execute("DELETE FROM events WHERE observed_at < ?",
                                    (cutoff.isoformat(timespec="microseconds"),))
    return cursor.rowcount


def trim_events(connection: sqlite3.Connection, max_events: int) -> int:
    """Keep the newest facts inside the caller's transaction; never remove checkpoints."""
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events <= 0:
        raise ValueError("max_events must be a positive integer")
    if not connection.in_transaction:
        raise RuntimeError("event trimming requires a transaction")
    cursor = connection.execute("""DELETE FROM events WHERE rowid IN (
        SELECT rowid FROM events ORDER BY observed_at DESC, cursor DESC LIMIT -1 OFFSET ?
    )""", (max_events,))
    return cursor.rowcount


def trim_snapshots(connection: sqlite3.Connection, max_snapshots: int) -> int:
    """Keep newest snapshots inside the caller's transaction with cascading children."""
    if (isinstance(max_snapshots, bool) or not isinstance(max_snapshots, int)
            or max_snapshots <= 0):
        raise ValueError("max_snapshots must be a positive integer")
    if not connection.in_transaction:
        raise RuntimeError("snapshot trimming requires a transaction")
    cursor = connection.execute("""DELETE FROM snapshots WHERE id IN (
        SELECT id FROM snapshots ORDER BY id DESC LIMIT -1 OFFSET ?
    )""", (max_snapshots,))
    return cursor.rowcount


def delete_snapshots_before(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete snapshots strictly before a UTC cutoff and return the number removed.

    Child observation and quality rows are removed by schema foreign-key cascades.
    This function does not choose a retention policy or run automatically.
    """
    if cutoff.tzinfo is None or cutoff.utcoffset() != UTC.utcoffset(cutoff):
        raise ValueError("retention cutoff must be timezone-aware UTC")
    with transaction(connection):
        cursor = connection.execute("DELETE FROM snapshots WHERE observed_at < ?", (cutoff.isoformat(),))
    return cursor.rowcount


def delete_resolved_incidents_before(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete derived incidents resolved before a UTC cutoff, preserving active state."""
    if cutoff.tzinfo is None or cutoff.utcoffset() != UTC.utcoffset(cutoff):
        raise ValueError("incident retention cutoff must be timezone-aware UTC")
    with transaction(connection):
        cursor = connection.execute(
            "DELETE FROM incidents WHERE state = 'resolved' AND resolved_at < ?",
            (cutoff.isoformat(timespec="microseconds"),),
        )
    return cursor.rowcount
