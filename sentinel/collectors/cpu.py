"""Raw cumulative CPU counters. Utilisation belongs to a later two-sample analysis phase."""

from pathlib import Path

from sentinel.models import CPUObservation
from sentinel.models.common import CollectionResult
from .base import collect


def parse_cpu_stat(text: str) -> CPUObservation:
    line = next((line for line in text.splitlines() if line.startswith("cpu ")), None)
    if line is None:
        raise ValueError("aggregate cpu line missing")
    fields = line.split()[1:]
    if len(fields) < 4:
        raise ValueError("aggregate cpu line incomplete")
    counters = [int(value) for value in fields]
    if any(value < 0 for value in counters):
        raise ValueError("negative CPU counter")
    counters += [0] * (8 - len(counters))
    return CPUObservation(*counters[:8])


def collect_cpu(proc_root: Path = Path("/proc")) -> CollectionResult[CPUObservation]:
    return collect(lambda: parse_cpu_stat((proc_root / "stat").read_text(encoding="ascii")))
