"""Process-scoped ownership for one foreground runtime per Sentinel database."""

from __future__ import annotations

import fcntl
import os
import stat
import threading
from pathlib import Path

from sentinel.storage.database import resolve_database_path


RUNTIME_OWNERSHIP_EXIT_CODE = 3


class RuntimeOwnershipError(RuntimeError):
    """Base class for failures specific to runtime ownership."""


class RuntimeAlreadyOwned(RuntimeOwnershipError):
    """Another live runtime owns the selected database state."""


class RuntimeLease:
    """Hold a non-blocking Linux advisory lock for the complete runtime run.

    The lock file is durable but the ownership is not: the kernel releases the
    open-file-description lock when the process exits, including abnormal exits.
    A leftover lock file is therefore safe and is never deleted as stale state.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        database = resolve_database_path(database_path).resolve(strict=False)
        self.path = database.with_name(f"{database.name}.runtime.lock")
        self._descriptor: int | None = None
        self._lifecycle_lock = threading.Lock()

    @property
    def acquired(self) -> bool:
        with self._lifecycle_lock:
            return self._descriptor is not None

    def acquire(self) -> None:
        """Acquire immediately or raise without waiting behind another runtime."""
        with self._lifecycle_lock:
            if self._descriptor is not None:
                raise RuntimeAlreadyOwned("this runtime lease is already held")
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise OSError(
                        "runtime lock path must be a regular file with one link"
                    )
                os.fchmod(descriptor, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeAlreadyOwned(
                        "another Sentinel runtime owns this database"
                    ) from exc
            except BaseException:
                os.close(descriptor)
                raise
            self._descriptor = descriptor

    def release(self) -> None:
        """Release idempotently; closing the descriptor is the ownership boundary."""
        with self._lifecycle_lock:
            descriptor = self._descriptor
            self._descriptor = None
            if descriptor is not None:
                os.close(descriptor)

    def __enter__(self) -> RuntimeLease:
        self.acquire()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.release()
