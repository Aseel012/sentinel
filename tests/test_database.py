import importlib
import sqlite3
import os
import stat
import tempfile
import unittest
from pathlib import Path

from sentinel.storage.database import (DEFAULT_BUSY_TIMEOUT_MS, database_connection, open_connection,
                                       resolve_database_path, transaction)
from sentinel.storage.health import inspect_database
from sentinel.storage.migrations import initialize_schema
from sentinel.storage.schema import SCHEMA_VERSION


class DatabasePathTests(unittest.TestCase):
    def test_explicit_path_is_preserved(self) -> None:
        path = Path("relative") / "sentinel.db"
        self.assertEqual(resolve_database_path(path), path)

    def test_uses_absolute_xdg_data_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(resolve_database_path(environ={"XDG_DATA_HOME": str(root)}),
                             root / "sentinel" / "sentinel.db")

    def test_falls_back_for_blank_or_relative_xdg_data_home(self) -> None:
        home = Path("/safe/test-home")
        expected = home / ".local" / "share" / "sentinel" / "sentinel.db"
        self.assertEqual(resolve_database_path(environ={"XDG_DATA_HOME": "  "}, home=home), expected)
        self.assertEqual(resolve_database_path(environ={"XDG_DATA_HOME": "relative"}, home=home), expected)

    def test_importing_database_module_does_not_create_default_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            expected = resolve_database_path(environ={}, home=home)
            import sentinel.storage.database as database
            importlib.reload(database)
            self.assertFalse(expected.exists())


class DatabaseConnectionTests(unittest.TestCase):
    def test_open_creates_parent_and_configures_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "sentinel.db"
            connection = open_connection(path)
            try:
                self.assertTrue(path.exists())
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], DEFAULT_BUSY_TIMEOUT_MS)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            finally:
                connection.close()

    def test_database_and_wal_files_are_private_despite_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "sentinel.db"
            previous_umask = os.umask(0o022)
            try:
                connection = open_connection(path)
            finally:
                os.umask(previous_umask)
            try:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                wal_path = path.with_name("sentinel.db-wal")
                if wal_path.exists():
                    self.assertEqual(stat.S_IMODE(wal_path.stat().st_mode), 0o600)
            finally:
                connection.close()

    def test_context_manager_closes_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            with database_connection(path) as connection:
                connection.execute("SELECT 1")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_repeated_opening_uses_same_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            with database_connection(path) as connection:
                connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
                connection.execute("INSERT INTO sample VALUES (7)")
                connection.commit()
            with database_connection(path) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], 7)

    def test_transaction_commits_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            with database_connection(path) as connection:
                connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
                connection.commit()
                with transaction(connection):
                    connection.execute("INSERT INTO sample VALUES (1)")
                with self.assertRaises(RuntimeError):
                    with transaction(connection):
                        connection.execute("INSERT INTO sample VALUES (2)")
                        raise RuntimeError("force rollback")
                self.assertEqual(connection.execute("SELECT value FROM sample ORDER BY value").fetchall(), [(1,)])

    def test_write_lock_timeout_is_bounded_and_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            first = open_connection(path, busy_timeout_ms=20)
            second = open_connection(path, busy_timeout_ms=20)
            try:
                first.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
                first.commit()
                first.execute("BEGIN IMMEDIATE")
                first.execute("INSERT INTO sample VALUES (1)")
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    with transaction(second):
                        second.execute("INSERT INTO sample VALUES (2)")
                first.commit()
                self.assertEqual(
                    second.execute("SELECT value FROM sample").fetchall(),
                    [(1,)],
                )
            finally:
                first.close()
                second.close()

    def test_rejects_invalid_transaction_mode_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            with self.assertRaises(ValueError):
                open_connection(path, busy_timeout_ms=-1)
            with self.assertRaises(TypeError):
                open_connection(path, busy_timeout_ms=True)
            with database_connection(path) as connection:
                with self.assertRaises(ValueError):
                    with transaction(connection, mode="invalid"):
                        pass

    def test_health_inspection_reports_initialized_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with database_connection(Path(directory) / "sentinel.db") as connection:
                initialize_schema(connection)
                health = inspect_database(connection)
        self.assertTrue(health.integrity_ok)
        self.assertEqual(health.schema_version, SCHEMA_VERSION)
        self.assertEqual(health.detail, "ok")
