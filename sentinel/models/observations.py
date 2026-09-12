"""Typed observations. Models deliberately have no system access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MemoryObservation:
    total_bytes: int
    available_bytes: int
    free_bytes: int
    buffers_bytes: int | None
    cached_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None


@dataclass(frozen=True, slots=True)
class CPUObservation:
    user_ticks: int
    nice_ticks: int
    system_ticks: int
    idle_ticks: int
    iowait_ticks: int
    irq_ticks: int
    softirq_ticks: int
    steal_ticks: int

    @property
    def total_ticks(self) -> int:
        return sum((self.user_ticks, self.nice_ticks, self.system_ticks, self.idle_ticks,
                    self.iowait_ticks, self.irq_ticks, self.softirq_ticks, self.steal_ticks))


@dataclass(frozen=True, slots=True)
class SystemObservation:
    hostname: str
    operating_system: str
    kernel: str
    architecture: str
    uptime_seconds: float


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    pid: int
    ppid: int
    name: str
    state: str
    rss_bytes: int
    virtual_memory_bytes: int | None
    threads: int
    cpu_time_ticks: int | None
    start_time_ticks: int | None
    command: str | None
    executable: str | None

    @property
    def lifetime_id(self) -> str | None:
        """Stable only for a process lifetime, preventing PID reuse confusion."""
        return None if self.start_time_ticks is None else f"{self.pid}:{self.start_time_ticks}"


@dataclass(frozen=True, slots=True)
class DiskObservation:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


@dataclass(frozen=True, slots=True)
class NetworkObservation:
    interface: str
    receive_bytes: int
    transmit_bytes: int
    receive_packets: int | None
    transmit_packets: int | None
    receive_errors: int | None
    transmit_errors: int | None
    receive_drops: int | None
    transmit_drops: int | None


@dataclass(frozen=True, slots=True)
class ServiceObservation:
    name: str
    load_state: str
    active_state: str
    sub_state: str


@dataclass(frozen=True, slots=True)
class EventObservation:
    """Bounded, normalized journal event facts; cursor is the source identity."""

    cursor: str
    timestamp: datetime
    source: str
    priority: int | None
    unit: str | None
    pid: int | None
    comm: str | None
    message: str
    boot_id: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JournalBatch:
    """Accepted source records and the last safely consumed journal identity."""

    events: tuple[EventObservation, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    timestamp: datetime
    system: SystemObservation | None
    memory: MemoryObservation | None
    cpu: CPUObservation | None
    processes: tuple[ProcessObservation, ...]
    disk: tuple[DiskObservation, ...]
    network: tuple[NetworkObservation, ...]
    services: tuple[ServiceObservation, ...]
    results: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()
