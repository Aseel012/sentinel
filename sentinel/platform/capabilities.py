"""Capability reporting is explicit: optional features cannot masquerade as empty data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from shutil import which

from .detection import PlatformInfo

class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    state: CapabilityState
    detail: str | None = None


def detect_capabilities(info: PlatformInfo, proc_root: Path = Path("/proc")) -> tuple[Capability, ...]:
    if not info.is_linux:
        return tuple(Capability(name, CapabilityState.UNSUPPORTED, "Linux is required") for name in
                     ("cpu", "memory", "system", "processes", "disk", "network", "systemd", "journal"))
    readable = lambda name: (proc_root / name).is_file() and (proc_root / name).exists()
    process_state = CapabilityState.SUPPORTED if proc_root.is_dir() else CapabilityState.UNAVAILABLE
    return (
        Capability("cpu", CapabilityState.SUPPORTED if readable("stat") else CapabilityState.UNAVAILABLE),
        Capability("memory", CapabilityState.SUPPORTED if readable("meminfo") else CapabilityState.UNAVAILABLE),
        Capability("system", CapabilityState.SUPPORTED if readable("uptime") else CapabilityState.DEGRADED),
        Capability("processes", process_state),
        Capability("disk", CapabilityState.SUPPORTED),
        Capability("network", CapabilityState.SUPPORTED if readable("net/dev") else CapabilityState.UNAVAILABLE),
        Capability("systemd", CapabilityState.SUPPORTED if which("systemctl") else CapabilityState.UNAVAILABLE),
        Capability("journal", CapabilityState.SUPPORTED if which("journalctl") else CapabilityState.UNAVAILABLE),
        Capability("notifications", CapabilityState.NOT_IMPLEMENTED),
    )
