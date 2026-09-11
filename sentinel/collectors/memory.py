"""Linux memory collector based on /proc/meminfo."""

from pathlib import Path

from sentinel.models import MemoryObservation
from sentinel.models.common import CollectionResult
from .base import collect


def parse_meminfo(text: str) -> MemoryObservation:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        parts = remainder.split()
        if not parts:
            continue
        value = int(parts[0])
        unit = parts[1].lower() if len(parts) > 1 else "b"
        if unit not in ("kb", "b"):
            raise ValueError("unrecognized meminfo unit")
        values[key] = value * (1024 if unit == "kb" else 1)
    required = ("MemTotal", "MemAvailable", "MemFree")
    if any(key not in values for key in required):
        raise ValueError("meminfo missing required fields")
    return MemoryObservation(values["MemTotal"], values["MemAvailable"], values["MemFree"],
                             values.get("Buffers"), values.get("Cached"), values.get("SwapTotal"),
                             values.get("SwapFree"))


def collect_memory(proc_root: Path = Path("/proc")) -> CollectionResult[MemoryObservation]:
    return collect(lambda: parse_meminfo((proc_root / "meminfo").read_text(encoding="ascii")))
