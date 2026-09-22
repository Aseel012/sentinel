"""Platform identity without assuming a particular distribution."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PlatformInfo:
    is_linux: bool
    operating_system: str
    kernel: str
    architecture: str
    proc_available: bool
    sys_available: bool

def detect_platform(proc_root: Path = Path("/proc"), sys_root: Path = Path("/sys")) -> PlatformInfo:
    return PlatformInfo(
        is_linux=sys.platform.startswith("linux"),
        operating_system=platform.system() or "unknown",
        kernel=platform.release() or "unknown",
        architecture=platform.machine() or "unknown",
        proc_available=proc_root.is_dir(),
        sys_available=sys_root.is_dir(),
    )
