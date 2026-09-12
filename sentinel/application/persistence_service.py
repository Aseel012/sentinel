"""Application service that explicitly records collected snapshots in local history."""

from __future__ import annotations

from pathlib import Path

from sentinel.storage.database import database_connection
from sentinel.storage.migrations import initialize_schema
from sentinel.storage.snapshots import SnapshotRepository, StoredSnapshot

from .snapshot_service import SnapshotCollection, SnapshotService


class PersistentSnapshotService:
    """Collect and persist snapshots without exposing SQLite to the CLI or collectors."""

    def __init__(self, database_path: str | Path | None = None,
                 snapshot_service: SnapshotService | None = None, *,
                 max_snapshots: int | None = None) -> None:
        if max_snapshots is not None and (
            isinstance(max_snapshots, bool) or not isinstance(max_snapshots, int)
            or max_snapshots <= 0
        ):
            raise ValueError("max_snapshots must be a positive integer or None")
        self._database_path = database_path
        self._snapshot_service = snapshot_service or SnapshotService()
        self._max_snapshots = max_snapshots

    def record(self) -> StoredSnapshot:
        """Collect the current machine state and save it atomically."""
        return self.record_collection(self._snapshot_service.collect())

    def record_collection(self, collection: SnapshotCollection) -> StoredSnapshot:
        """Save an already-collected snapshot, useful for controlled callers and tests."""
        with database_connection(self._database_path) as connection:
            initialize_schema(connection)
            repository = SnapshotRepository(connection)
            snapshot_id = repository.save(collection, max_snapshots=self._max_snapshots)
            stored = repository.get(snapshot_id)
            if stored is None:
                raise RuntimeError("persisted snapshot could not be read back")
            return stored
