"""Explicit, transactional schema migration runner."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from .database import transaction
from .schema import (SCHEMA_VERSION, create_schema_v1, create_schema_v2, create_schema_v3,
                     validate_schema_v1, validate_schema_v2, validate_schema_v3)

Migration = Callable[[sqlite3.Connection], None]


class SchemaMigrationError(RuntimeError):
    """The database cannot safely be treated as the requested Sentinel schema."""


class FutureSchemaError(SchemaMigrationError):
    """The database was created by a newer Sentinel schema implementation."""


def _migration_1(connection: sqlite3.Connection) -> None:
    create_schema_v1(connection)


def _migration_2(connection: sqlite3.Connection) -> None:
    create_schema_v2(connection)


MIGRATIONS: Mapping[int, Migration] = {1: _migration_1, 2: _migration_2, 3: create_schema_v3}
_SENTINEL_V1_TABLES = frozenset({
    "snapshots", "system_observations", "memory_observations", "cpu_observations",
    "process_observations", "disk_observations", "network_observations",
    "service_observations", "collection_results", "collection_warnings",
})
_SENTINEL_TABLES = _SENTINEL_V1_TABLES | frozenset({"events", "event_checkpoints", "event_collection_results"})
_SCHEMA_V1_TABLES = frozenset({"schema_migrations"}) | _SENTINEL_V1_TABLES


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone()
    return row is not None


def installed_schema_version(connection: sqlite3.Connection) -> int:
    """Return zero for an uninitialized database or its highest applied migration."""
    if not _table_exists(connection, "schema_migrations"):
        existing_tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if existing_tables & _SENTINEL_TABLES:
            raise SchemaMigrationError("Sentinel tables exist without schema migration metadata")
        return 0
    versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    if not versions:
        return 0
    if any(not isinstance(version, int) or version <= 0 for version in versions):
        raise SchemaMigrationError("schema migration metadata contains an invalid version")
    if versions != list(range(1, versions[-1] + 1)):
        raise SchemaMigrationError("schema migration metadata has a version gap")
    if versions[-1] >= 1:
        existing_tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing_tables = _SCHEMA_V1_TABLES - existing_tables
        if missing_tables:
            raise SchemaMigrationError("database schema is missing required Sentinel tables")
        if not validate_schema_v1(connection):
            raise SchemaMigrationError("database schema does not match Sentinel schema version 1")
    if versions[-1] >= 2 and not validate_schema_v2(connection):
        raise SchemaMigrationError("database schema does not match Sentinel schema version 2")
    if versions[-1] >= 3 and not validate_schema_v3(connection):
        raise SchemaMigrationError("database schema does not match Sentinel schema version 3")
    return versions[-1]


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    migrations: Mapping[int, Migration] = MIGRATIONS,
    target_version: int = SCHEMA_VERSION,
) -> int:
    """Apply each missing migration once, atomically, and return its final version."""
    if target_version < 0:
        raise ValueError("target_version cannot be negative")
    with transaction(connection):
        installed_version = installed_schema_version(connection)
        if installed_version > target_version:
            raise FutureSchemaError(
                f"database schema version {installed_version} is newer than supported version {target_version}"
            )
        for version in range(installed_version + 1, target_version + 1):
            migration = migrations.get(version)
            if migration is None:
                raise SchemaMigrationError(f"missing migration for schema version {version}")
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
        return installed_schema_version(connection)


def initialize_schema(connection: sqlite3.Connection) -> int:
    """Initialize a fresh database or advance it to this program's schema version."""
    return apply_migrations(connection)
