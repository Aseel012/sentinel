import sqlite3
import tempfile
import unittest
from pathlib import Path

from sentinel.storage.database import database_connection
from sentinel.storage.migrations import (FutureSchemaError, SchemaMigrationError, apply_migrations,
                                         initialize_schema, installed_schema_version)
from sentinel.storage.schema import SCHEMA_VERSION


class SchemaMigrationTests(unittest.TestCase):
    def connection(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return database_connection(Path(directory.name) / "sentinel.db")

    def test_fresh_initialization_creates_versioned_relational_schema(self) -> None:
        with self.connection() as connection:
            self.assertEqual(installed_schema_version(connection), 0)
            self.assertEqual(initialize_schema(connection), SCHEMA_VERSION)
            self.assertEqual(installed_schema_version(connection), SCHEMA_VERSION)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"snapshots", "system_observations", "memory_observations", "cpu_observations",
                         "process_observations", "disk_observations", "network_observations",
                         "service_observations", "collection_results", "collection_warnings",
                         "events", "event_checkpoints", "schema_migrations", "incidents",
                         "incident_evidence", "incident_limitations",
                         "incident_evidence_limitations"}.issubset(tables))

    def test_version_one_database_advances_to_event_schema_without_losing_snapshots(self) -> None:
        with self.connection() as connection:
            self.assertEqual(apply_migrations(connection, target_version=1), 1)
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", ("2026-01-02T03:04:05+00:00",))
            connection.commit()
            self.assertEqual(initialize_schema(connection), SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], SCHEMA_VERSION)

    def test_initialization_is_idempotent_and_preserves_existing_data(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", ("2026-01-02T03:04:05+00:00",))
            connection.commit()
            self.assertEqual(initialize_schema(connection), SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)

    def test_required_indexes_and_nullable_memory_fields_exist(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            indexes = {row[1] for row in connection.execute("SELECT * FROM sqlite_master WHERE type = 'index'")}
            self.assertTrue({"idx_snapshots_observed_at", "idx_processes_lifetime", "idx_disks_path_snapshot",
                             "idx_networks_interface_snapshot", "idx_services_name_snapshot"}.issubset(indexes))
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", ("2026-01-02T03:04:05+00:00",))
            connection.execute("""INSERT INTO memory_observations(
                snapshot_id, total_bytes, available_bytes, free_bytes, buffers_bytes, cached_bytes,
                swap_total_bytes, swap_free_bytes
            ) VALUES (1, 10, 9, 8, NULL, NULL, NULL, NULL)""")
            self.assertEqual(connection.execute("SELECT buffers_bytes FROM memory_observations").fetchone()[0], None)

    def test_foreign_keys_and_retention_cascade_are_enforced(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO disk_observations(snapshot_id, path, total_bytes, used_bytes, free_bytes) VALUES (99, '/', 1, 1, 0)")
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", ("2026-01-02T03:04:05+00:00",))
            connection.execute("INSERT INTO disk_observations(snapshot_id, path, total_bytes, used_bytes, free_bytes) VALUES (1, '/', 1, 1, 0)")
            connection.execute("DELETE FROM snapshots WHERE id = 1")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM disk_observations").fetchone()[0], 0)

    def test_process_pid_reuse_is_distinguished_by_start_ticks(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            connection.executemany("INSERT INTO snapshots(observed_at) VALUES (?)", [
                ("2026-01-02T03:04:05+00:00",), ("2026-01-02T03:04:06+00:00",),
            ])
            connection.executemany("""INSERT INTO process_observations(
                snapshot_id, pid, ppid, name, state, rss_bytes, virtual_memory_bytes, threads,
                cpu_time_ticks, start_time_ticks, command, executable
            ) VALUES (?, 42, 1, 'worker', 'S', 1, NULL, 1, NULL, ?, NULL, NULL)""", [(1, 500), (2, 900)])
            rows = connection.execute("SELECT pid, start_time_ticks FROM process_observations ORDER BY snapshot_id").fetchall()
            self.assertEqual(rows, [(42, 500), (42, 900)])

    def test_collection_quality_preserves_partial_and_failure_states(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", ("2026-01-02T03:04:05+00:00",))
            connection.execute("""INSERT INTO collection_results(
                snapshot_id, collector_name, status, collected_at, duration_seconds, error_code, error_message
            ) VALUES (1, 'processes', 'partial', ?, 0.1, NULL, NULL)""", ("2026-01-02T03:04:05+00:00",))
            connection.execute("""INSERT INTO collection_warnings(snapshot_id, collector_name, warning_index, warning)
                                VALUES (1, 'processes', 0, 'process exited during collection')""")
            connection.execute("""INSERT INTO collection_results(
                snapshot_id, collector_name, status, collected_at, duration_seconds, error_code, error_message
            ) VALUES (1, 'services', 'unsupported', ?, 0.0, 'systemd_absent', 'systemctl unavailable')""",
                               ("2026-01-02T03:04:05+00:00",))
            self.assertEqual(connection.execute("SELECT status FROM collection_results ORDER BY collector_name").fetchall(),
                             [("partial",), ("unsupported",)])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO collection_results(
                    snapshot_id, collector_name, status, collected_at, duration_seconds
                ) VALUES (1, 'cpu', 'unknown', '2026-01-02T03:04:05+00:00', 0.0)""")

    def test_failing_migration_rolls_back_its_schema_changes(self) -> None:
        def failing_migration(connection: sqlite3.Connection) -> None:
            connection.execute("CREATE TABLE failed_marker (value INTEGER)")
            raise RuntimeError("migration failed")

        with self.connection() as connection:
            with self.assertRaises(RuntimeError):
                apply_migrations(connection, migrations={1: failing_migration}, target_version=1)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("failed_marker", tables)
            self.assertEqual(installed_schema_version(connection), 0)

    def test_newer_schema_is_rejected_without_modification(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                               (SCHEMA_VERSION + 1, "2026-01-02T03:04:05+00:00"))
            connection.commit()
            with self.assertRaises(FutureSchemaError):
                initialize_schema(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], SCHEMA_VERSION + 1)

    def test_version_two_event_timestamps_and_facts_survive_migration(self) -> None:
        with self.connection() as connection:
            apply_migrations(connection, target_version=2)
            connection.execute("INSERT INTO events(cursor, observed_at, source, message) VALUES (?, ?, ?, ?)",
                               ("old", "2026-01-02T03:04:05+00:00", "journal", "keep"))
            connection.commit()
            initialize_schema(connection)
            self.assertEqual(connection.execute("SELECT cursor, observed_at, message, warnings FROM events").fetchone(),
                             ("old", "2026-01-02T03:04:05.000000+00:00", "keep", '["legacy_quality_unknown"]'))

    def test_version_four_incident_evidence_survives_with_unknown_structured_fact(self) -> None:
        with self.connection() as connection:
            apply_migrations(connection, target_version=4)
            incident_id = "a" * 64
            timestamp = "2026-01-02T03:04:05.000000+00:00"
            connection.execute("""INSERT INTO incidents(
                incident_id, rule_id, subject_type, subject_id, state, started_at,
                last_observed_at, resolved_at
            ) VALUES (?, 'legacy-rule', 'service', 'api.service', 'active', ?, ?, NULL)""",
                               (incident_id, timestamp, timestamp))
            connection.execute("""INSERT INTO incident_evidence(
                incident_id, kind, evidence_id, observed_at, subject_type, subject_id, reason, summary
            ) VALUES (?, 'service_change', 'legacy-anchor', ?, 'service', 'api.service',
                      'service_change_anchor', 'legacy summary')""", (incident_id, timestamp))
            connection.commit()
            self.assertEqual(initialize_schema(connection), SCHEMA_VERSION)
            self.assertEqual(connection.execute(
                "SELECT evidence_id, fact FROM incident_evidence"
            ).fetchone(), ("legacy-anchor", "unknown"))

    def test_sentinel_tables_without_migration_metadata_are_rejected(self) -> None:
        with self.connection() as connection:
            connection.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY)")
            connection.commit()
            with self.assertRaises(SchemaMigrationError):
                initialize_schema(connection)

    def test_migration_metadata_with_missing_required_table_is_rejected(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            connection.execute("DROP TABLE service_observations")
            connection.commit()
            with self.assertRaises(SchemaMigrationError):
                initialize_schema(connection)

    def test_migration_metadata_with_incomplete_table_is_rejected(self) -> None:
        with self.connection() as connection:
            initialize_schema(connection)
            connection.execute("DROP TABLE service_observations")
            connection.execute("CREATE TABLE service_observations (id INTEGER PRIMARY KEY)")
            connection.commit()
            with self.assertRaises(SchemaMigrationError):
                initialize_schema(connection)
