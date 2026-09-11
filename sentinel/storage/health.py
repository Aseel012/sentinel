"""Read-only database health inspection for support and future CLI integration."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .migrations import installed_schema_version


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    schema_version: int
    sqlite_version: str
    integrity_ok: bool
    detail: str


def inspect_database(connection: sqlite3.Connection) -> DatabaseHealth:
    """Run SQLite's read-only integrity check and expose its precise result.

    Operational SQLite errors intentionally propagate: a database that cannot be
    queried must not be reported as healthy or silently reset.
    """
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None:
        raise RuntimeError("SQLite integrity check returned no result")
    detail = str(result[0])
    return DatabaseHealth(installed_schema_version(connection), sqlite3.sqlite_version, detail == "ok", detail)
