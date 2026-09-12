"""Local persistence boundaries for Phase 2 and later."""

from .database import database_connection, open_connection, resolve_database_path, transaction
from .migrations import FutureSchemaError, SchemaMigrationError, initialize_schema, installed_schema_version
from .snapshots import SnapshotRepository, SnapshotStorageError, StoredSnapshot
from .retention import delete_snapshots_before
from .health import DatabaseHealth, inspect_database
from .events import (JournalCheckpointConflict, delete_events_before, journal_cursor,
                     latest_journal_quality, load_events, store_journal_batch, store_journal_result)

__all__ = ["DatabaseHealth", "FutureSchemaError", "SchemaMigrationError", "SnapshotRepository",
           "SnapshotStorageError", "StoredSnapshot", "database_connection", "delete_snapshots_before",
           "delete_events_before", "initialize_schema", "inspect_database", "installed_schema_version",
           "journal_cursor", "open_connection", "resolve_database_path", "store_journal_batch", "transaction",
           "JournalCheckpointConflict", "latest_journal_quality", "load_events", "store_journal_result"]
