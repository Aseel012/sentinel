"""Static, relational schema definitions for Sentinel persistent history.

The schema intentionally stores queryable observation columns instead of a single
snapshot JSON document.  Raw CPU and network counters are retained because rates
need two samples and belong to a later analysis phase.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3

SCHEMA_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY,
        observed_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snapshots_observed_at ON snapshots(observed_at)",
    """
    CREATE TABLE IF NOT EXISTS system_observations (
        snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
        hostname TEXT NOT NULL,
        operating_system TEXT NOT NULL,
        kernel TEXT NOT NULL,
        architecture TEXT NOT NULL,
        uptime_seconds REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_observations (
        snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
        total_bytes INTEGER NOT NULL,
        available_bytes INTEGER NOT NULL,
        free_bytes INTEGER NOT NULL,
        buffers_bytes INTEGER,
        cached_bytes INTEGER,
        swap_total_bytes INTEGER,
        swap_free_bytes INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cpu_observations (
        snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
        user_ticks INTEGER NOT NULL,
        nice_ticks INTEGER NOT NULL,
        system_ticks INTEGER NOT NULL,
        idle_ticks INTEGER NOT NULL,
        iowait_ticks INTEGER NOT NULL,
        irq_ticks INTEGER NOT NULL,
        softirq_ticks INTEGER NOT NULL,
        steal_ticks INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS process_observations (
        id INTEGER PRIMARY KEY,
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        pid INTEGER NOT NULL,
        ppid INTEGER NOT NULL,
        name TEXT NOT NULL,
        state TEXT NOT NULL,
        rss_bytes INTEGER NOT NULL,
        virtual_memory_bytes INTEGER,
        threads INTEGER NOT NULL,
        cpu_time_ticks INTEGER,
        start_time_ticks INTEGER,
        command TEXT,
        executable TEXT,
        UNIQUE(snapshot_id, pid)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_processes_snapshot_id ON process_observations(snapshot_id)",
    """
    CREATE INDEX IF NOT EXISTS idx_processes_lifetime
    ON process_observations(pid, start_time_ticks, snapshot_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS disk_observations (
        id INTEGER PRIMARY KEY,
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        total_bytes INTEGER NOT NULL,
        used_bytes INTEGER NOT NULL,
        free_bytes INTEGER NOT NULL,
        UNIQUE(snapshot_id, path)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_disks_path_snapshot ON disk_observations(path, snapshot_id)",
    """
    CREATE TABLE IF NOT EXISTS network_observations (
        id INTEGER PRIMARY KEY,
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        interface TEXT NOT NULL,
        receive_bytes INTEGER NOT NULL,
        transmit_bytes INTEGER NOT NULL,
        receive_packets INTEGER,
        transmit_packets INTEGER,
        receive_errors INTEGER,
        transmit_errors INTEGER,
        receive_drops INTEGER,
        transmit_drops INTEGER,
        UNIQUE(snapshot_id, interface)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_networks_interface_snapshot
    ON network_observations(interface, snapshot_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS service_observations (
        id INTEGER PRIMARY KEY,
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        load_state TEXT NOT NULL,
        active_state TEXT NOT NULL,
        sub_state TEXT NOT NULL,
        UNIQUE(snapshot_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_services_name_snapshot
    ON service_observations(name, snapshot_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS collection_results (
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        collector_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'success', 'partial', 'permission_denied', 'unsupported',
            'transient_failure', 'invalid_data'
        )),
        collected_at TEXT NOT NULL,
        duration_seconds REAL NOT NULL CHECK (duration_seconds >= 0),
        error_code TEXT,
        error_message TEXT,
        PRIMARY KEY (snapshot_id, collector_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collection_warnings (
        snapshot_id INTEGER NOT NULL,
        collector_name TEXT NOT NULL,
        warning_index INTEGER NOT NULL CHECK (warning_index >= 0),
        warning TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, collector_name, warning_index),
        FOREIGN KEY (snapshot_id, collector_name)
            REFERENCES collection_results(snapshot_id, collector_name)
            ON DELETE CASCADE
    )
    """,
)

_REQUIRED_COLUMNS = {
    "snapshots": {"id", "observed_at"},
    "system_observations": {"snapshot_id", "hostname", "operating_system", "kernel", "architecture", "uptime_seconds"},
    "memory_observations": {"snapshot_id", "total_bytes", "available_bytes", "free_bytes", "buffers_bytes", "cached_bytes", "swap_total_bytes", "swap_free_bytes"},
    "cpu_observations": {"snapshot_id", "user_ticks", "nice_ticks", "system_ticks", "idle_ticks", "iowait_ticks", "irq_ticks", "softirq_ticks", "steal_ticks"},
    "process_observations": {"id", "snapshot_id", "pid", "ppid", "name", "state", "rss_bytes", "virtual_memory_bytes", "threads", "cpu_time_ticks", "start_time_ticks", "command", "executable"},
    "disk_observations": {"id", "snapshot_id", "path", "total_bytes", "used_bytes", "free_bytes"},
    "network_observations": {"id", "snapshot_id", "interface", "receive_bytes", "transmit_bytes", "receive_packets", "transmit_packets", "receive_errors", "transmit_errors", "receive_drops", "transmit_drops"},
    "service_observations": {"id", "snapshot_id", "name", "load_state", "active_state", "sub_state"},
    "collection_results": {"snapshot_id", "collector_name", "status", "collected_at", "duration_seconds", "error_code", "error_message"},
    "collection_warnings": {"snapshot_id", "collector_name", "warning_index", "warning"},
}
_REQUIRED_INDEXES = {"idx_snapshots_observed_at", "idx_processes_snapshot_id", "idx_processes_lifetime",
                     "idx_disks_path_snapshot", "idx_networks_interface_snapshot", "idx_services_name_snapshot"}

SCHEMA_V2_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS events (
        cursor TEXT PRIMARY KEY,
        observed_at TEXT NOT NULL,
        source TEXT NOT NULL,
        priority INTEGER,
        unit TEXT,
        pid INTEGER,
        comm TEXT,
        message TEXT NOT NULL,
        boot_id TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_observed_at ON events(observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_unit_observed_at ON events(unit, observed_at)",
    """
    CREATE TABLE IF NOT EXISTS event_checkpoints (
        source TEXT PRIMARY KEY,
        cursor TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
)


def create_schema_v1(connection: sqlite3.Connection) -> None:
    """Create schema version 1 inside the caller's transaction."""
    for statement in SCHEMA_V1_STATEMENTS:
        connection.execute(statement)


def create_schema_v2(connection: sqlite3.Connection) -> None:
    """Create independent event storage and source cursors inside a migration."""
    for statement in SCHEMA_V2_STATEMENTS:
        connection.execute(statement)


def create_schema_v3(connection: sqlite3.Connection) -> None:
    """Preserve event quality and the latest collection outcome."""
    from datetime import UTC, datetime

    connection.execute("ALTER TABLE events ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]'")
    connection.execute("UPDATE events SET warnings = '[\"legacy_quality_unknown\"]'")
    connection.execute("""CREATE TABLE event_collection_results (
        source TEXT PRIMARY KEY CHECK(source = 'journal'),
        status TEXT NOT NULL CHECK(status IN ('success', 'partial', 'permission_denied',
            'unsupported', 'transient_failure', 'invalid_data')),
        collected_at TEXT NOT NULL, duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0),
        event_count INTEGER CHECK(event_count >= 0), error_code TEXT, error_message TEXT,
        warnings TEXT NOT NULL
    )""")
    # V2 used variable precision ISO strings. Fixed precision preserves SQL time ordering.
    for table, identity, column in (("events", "cursor", "observed_at"),
                                    ("event_checkpoints", "source", "updated_at")):
        rows = connection.execute(f"SELECT {identity}, {column} FROM {table}")
        for key, value in rows:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
                raise ValueError("stored event timestamps must be timezone-aware UTC")
            timestamp = parsed.isoformat(timespec="microseconds")
            connection.execute(f"UPDATE {table} SET {column} = ? WHERE {identity} = ?", (timestamp, key))


def validate_schema_v1(connection: sqlite3.Connection) -> bool:
    """Verify that a database claiming schema v1 has its expected query structure."""
    for table, expected_columns in _REQUIRED_COLUMNS.items():
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not expected_columns.issubset(columns):
            return False
    indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    return _REQUIRED_INDEXES.issubset(indexes)


def validate_schema_v2(connection: sqlite3.Connection) -> bool:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    checkpoint_columns = {row[1] for row in connection.execute("PRAGMA table_info(event_checkpoints)")}
    indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    return ({"cursor", "observed_at", "source", "priority", "unit", "pid", "comm", "message", "boot_id"}.issubset(columns)
            and {"source", "cursor", "updated_at"}.issubset(checkpoint_columns)
            and {"idx_events_observed_at", "idx_events_unit_observed_at"}.issubset(indexes))


def validate_schema_v3(connection: sqlite3.Connection) -> bool:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(event_collection_results)")}
    event_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    return "warnings" in event_columns and {"source", "status", "collected_at", "duration_seconds",
        "event_count", "error_code", "error_message", "warnings"}.issubset(columns)
