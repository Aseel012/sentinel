"""Host identity and uptime collector."""

import platform
import socket
from pathlib import Path

from sentinel.models import SystemObservation
from sentinel.models.common import CollectionResult
from .base import collect


def parse_uptime(text: str) -> float:
    value = float(text.split()[0])
    if value < 0:
        raise ValueError("negative uptime")
    return value


def collect_system(proc_root: Path = Path("/proc")) -> CollectionResult[SystemObservation]:
    def operation() -> SystemObservation:
        return SystemObservation(socket.gethostname(), platform.system() or "Linux",
                                 platform.release() or "unknown", platform.machine() or "unknown",
                                 parse_uptime((proc_root / "uptime").read_text(encoding="ascii")))
    return collect(operation)
