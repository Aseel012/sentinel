"""Local persistence boundaries for Phase 2 and later."""

from .database import database_connection, open_connection, resolve_database_path, transaction
from .migrations import FutureSchemaError, SchemaMigrationError, initialize_schema, installed_schema_version
from .snapshots import SnapshotRepository, SnapshotStorageError, StoredSnapshot
from .retention import delete_snapshots_before
from .health import DatabaseHealth, inspect_database

__all__ = ["DatabaseHealth", "FutureSchemaError", "SchemaMigrationError", "SnapshotRepository",
           "SnapshotStorageError", "StoredSnapshot", "database_connection", "delete_snapshots_before",
           "initialize_schema", "inspect_database", "installed_schema_version", "open_connection",
           "resolve_database_path", "transaction"]
