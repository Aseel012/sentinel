"""Filesystem capacity collector; it intentionally does not imply disk I/O."""

import os
from pathlib import Path

from sentinel.models import DiskObservation
from sentinel.models.common import CollectionResult
from .base import collect


def collect_disk(paths: tuple[Path, ...] = (Path("/"),)) -> CollectionResult[tuple[DiskObservation, ...]]:
    def operation() -> tuple[DiskObservation, ...]:
        observations = []
        for path in paths:
            stat = os.statvfs(path)
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            observations.append(DiskObservation(str(path), total, total - free, free))
        return tuple(observations)
    return collect(operation)
