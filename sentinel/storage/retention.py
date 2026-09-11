"""Manual retention primitives; policy and scheduling belong to later phases."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .database import transaction


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
