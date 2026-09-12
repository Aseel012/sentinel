"""Local persistence boundaries for Phase 2 and later."""

from .database import database_connection, open_connection, resolve_database_path, transaction
from .migrations import FutureSchemaError, SchemaMigrationError, initialize_schema, installed_schema_version
from .snapshots import SnapshotRepository, SnapshotStorageError, StoredSnapshot
from .retention import delete_resolved_incidents_before, delete_snapshots_before
from .health import DatabaseHealth, inspect_database
from .events import (EventWindow, JournalCheckpointConflict, delete_events_before, journal_cursor,
                     latest_journal_quality, load_event_window, load_events, store_journal_batch,
                     store_journal_result)
from .incidents import IncidentReconciliation, IncidentRepository, IncidentStorageError

__all__ = ["DatabaseHealth", "FutureSchemaError", "SchemaMigrationError", "SnapshotRepository",
           "SnapshotStorageError", "StoredSnapshot", "database_connection", "delete_snapshots_before",
           "delete_resolved_incidents_before", "IncidentReconciliation", "IncidentRepository",
           "IncidentStorageError",
           "delete_events_before", "initialize_schema", "inspect_database", "installed_schema_version",
           "journal_cursor", "open_connection", "resolve_database_path", "store_journal_batch", "transaction",
           "EventWindow", "JournalCheckpointConflict", "latest_journal_quality", "load_event_window",
           "load_events", "store_journal_result"]
