"""Common safe collection mechanics."""

from __future__ import annotations

import errno
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from sentinel.models.common import CollectionResult, CollectionStatus

T = TypeVar("T")


def collect(operation: Callable[[], T]) -> CollectionResult[T]:
    """Run an observation operation, preserving expected OS failure semantics."""
    started = time.monotonic()
    timestamp = datetime.now(UTC)
    try:
        value = operation()
    except PermissionError as exc:
        return CollectionResult(None, CollectionStatus.PERMISSION_DENIED, timestamp, time.monotonic() - started,
                                "permission_denied", "permission denied")
    except FileNotFoundError as exc:
        return CollectionResult(None, CollectionStatus.TRANSIENT_FAILURE, timestamp, time.monotonic() - started,
                                "not_found", "source disappeared during collection")
    except OSError as exc:
        code = "transient_failure" if exc.errno in (errno.ENOENT, errno.ESTALE, errno.EIO) else "os_error"
        return CollectionResult(None, CollectionStatus.TRANSIENT_FAILURE, timestamp, time.monotonic() - started,
                                code, "operating-system collection failure")
    except ValueError:
        return CollectionResult(None, CollectionStatus.INVALID_DATA, timestamp, time.monotonic() - started,
                                "invalid_data", "source data was malformed")
    return CollectionResult(value, CollectionStatus.SUCCESS, timestamp, time.monotonic() - started)
