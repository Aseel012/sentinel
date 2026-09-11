"""Shared immutable collection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import math
from typing import Generic, TypeVar

T = TypeVar("T")


class CollectionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED = "unsupported"
    TRANSIENT_FAILURE = "transient_failure"
    INVALID_DATA = "invalid_data"


@dataclass(frozen=True, slots=True)
class CollectionResult(Generic[T]):
    """A value together with an honest account of collection quality."""

    value: T | None
    status: CollectionStatus
    collected_at: datetime
    duration_seconds: float
    error_code: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.collected_at.tzinfo is None:
            raise ValueError("collected_at must be timezone-aware UTC")
        if self.collected_at.utcoffset() != UTC.utcoffset(self.collected_at):
            raise ValueError("collected_at must use UTC")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and non-negative")
        if self.status is CollectionStatus.SUCCESS and self.value is None:
            raise ValueError("a successful collection requires a value")


def utc_now() -> datetime:
    return datetime.now(UTC)
