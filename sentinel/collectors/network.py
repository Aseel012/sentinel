"""Raw cumulative network interface counters from /proc/net/dev."""

from pathlib import Path

from sentinel.models import NetworkObservation
from sentinel.models.common import CollectionResult
from .base import collect


def parse_network_dev(text: str) -> tuple[NetworkObservation, ...]:
    observations = []
    for line in text.splitlines()[2:]:
        interface, separator, data = line.partition(":")
        if not separator:
            raise ValueError("malformed network record")
        fields = data.split()
        if len(fields) < 16:
            raise ValueError("incomplete network record")
        counters = [int(item) for item in fields]
        if any(item < 0 for item in counters):
            raise ValueError("negative network counter")
        observations.append(NetworkObservation(interface.strip(), counters[0], counters[8], counters[1],
                                                counters[9], counters[2], counters[10], counters[3], counters[11]))
    return tuple(observations)


def collect_network(proc_root: Path = Path("/proc")) -> CollectionResult[tuple[NetworkObservation, ...]]:
    return collect(lambda: parse_network_dev((proc_root / "net" / "dev").read_text(encoding="ascii")))
