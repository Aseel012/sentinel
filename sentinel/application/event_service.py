"""Explicit orchestration for independent, cursor-backed journal events."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sentinel.collectors.journal import collect_journal
from sentinel.models import CollectionResult, CollectionStatus, JournalBatch
from sentinel.storage.database import database_connection
from sentinel.storage.events import DEFAULT_MAX_EVENTS, JournalCheckpointConflict, journal_cursor, store_journal_result
from sentinel.storage.migrations import initialize_schema


class JournalEventService:
    """Collect a bounded journal increment and durably checkpoint accepted records."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        collector: Callable[[str | None], CollectionResult[JournalBatch]] = collect_journal,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._database_path = database_path
        self._collector = collector
        self._max_events = max_events

    def record(self) -> CollectionResult[JournalBatch]:
        """Read from the stored cursor and persist only a valid returned batch."""
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            previous_cursor = journal_cursor(connection)
            result = self._collector(previous_cursor)
            try:
                store_journal_result(connection, result, expected_cursor=previous_cursor,
                                     max_events=self._max_events)
            except JournalCheckpointConflict:
                return CollectionResult(None, CollectionStatus.TRANSIENT_FAILURE, result.collected_at,
                                        result.duration_seconds, "journal_checkpoint_conflict",
                                        "another collector advanced journal history; retry from the saved cursor")
            return result
