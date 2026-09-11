"""SQLite connection lifecycle and configuration for Sentinel's local history.

This module deliberately owns neither schema nor repositories.  Each call opens a
new SQLite connection, which keeps ownership explicit and leaves SQLite's normal
single-thread connection rule in place.  Callers should use ``database_connection``
for ordinary work and ``transaction`` for a multi-statement write.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

DEFAULT_BUSY_TIMEOUT_MS = 5_000
_DEFAULT_DATA_SUBPATH = ("sentinel", "sentinel.db")
_TRANSACTION_MODES = frozenset({"DEFERRED", "IMMEDIATE", "EXCLUSIVE"})


def resolve_database_path(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return an explicit path or the XDG-compliant per-user default.

    A relative or blank ``XDG_DATA_HOME`` is ignored because the XDG base-directory
    specification requires an absolute directory.  ``home`` and ``environ`` are
    injectable solely to make callers and tests independent of a developer machine.
    No filesystem access occurs here.
    """
    if path is not None:
        return Path(path).expanduser()

    environment = os.environ if environ is None else environ
    raw_xdg = environment.get("XDG_DATA_HOME", "").strip()
    if raw_xdg:
        xdg_data_home = Path(raw_xdg).expanduser()
        if xdg_data_home.is_absolute():
            return xdg_data_home.joinpath(*_DEFAULT_DATA_SUBPATH)

    user_home = Path.home() if home is None else home
    return user_home.expanduser() / ".local" / "share" / Path(*_DEFAULT_DATA_SUBPATH)


def _prepare_parent(path: Path) -> None:
    """Create only the requested database's parent directory when opening it."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)


def configure_connection(
    connection: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> None:
    """Apply the small, deliberate set of SQLite settings Sentinel requires.

    Foreign keys protect future repository relationships.  WAL permits one writer
    alongside concurrent readers, which fits periodic sampling and history queries.
    The five-second busy timeout lets short lock contention settle without hiding a
    persistent lock error.  SQLite exceptions are intentionally allowed to surface.
    """
    if isinstance(busy_timeout_ms, bool) or not isinstance(busy_timeout_ms, int):
        raise TypeError("busy_timeout_ms must be an integer")
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms cannot be negative")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    connection.execute("PRAGMA journal_mode = WAL")


def open_connection(
    path: str | Path | None = None,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> sqlite3.Connection:
    """Open and configure a local SQLite database; the caller owns the result.

    This function may create the selected database, never during import or path
    resolution.  It does not catch SQLite or filesystem errors so callers can
    distinguish permission, lock, corruption, and storage failures.
    """
    database_path = resolve_database_path(path, environ=environ, home=home)
    _prepare_parent(database_path)
    connection = sqlite3.connect(database_path, timeout=busy_timeout_ms / 1_000)
    try:
        configure_connection(connection, busy_timeout_ms=busy_timeout_ms)
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def database_connection(
    path: str | Path | None = None,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection and close it reliably on every exit path."""
    connection = open_connection(path, busy_timeout_ms=busy_timeout_ms, environ=environ, home=home)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection, *, mode: str = "IMMEDIATE") -> Iterator[sqlite3.Connection]:
    """Run a bounded, explicit transaction that commits or rolls back atomically."""
    normalized_mode = mode.upper()
    if normalized_mode not in _TRANSACTION_MODES:
        raise ValueError(f"unsupported transaction mode: {mode}")
    if connection.in_transaction:
        raise RuntimeError("cannot start a managed transaction while one is already active")
    connection.execute(f"BEGIN {normalized_mode}")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
