"""Race-tolerant process collector with bounded, redacted command metadata."""

from __future__ import annotations

import os
import re
from pathlib import Path

from sentinel.models import ProcessObservation
from sentinel.models.common import CollectionResult, CollectionStatus
from .base import collect

_SECRET_NAME = r"token|password|passwd|secret|api[_-]?key|authorization"
_SECRET_ASSIGNMENT = re.compile(rf"(?i)({_SECRET_NAME})(=|:)([^\s]+)")
_SECRET_ARGUMENT = re.compile(rf"(?i)(--?(?:{_SECRET_NAME})\b)(\s+)([^\s]+)")
_SHORT_SECRET_ARGUMENT = re.compile(r"(?i)(^|\s)(-[pk])(\s+)([^\s]+)")
_SECRET_QUERY = re.compile(rf"(?i)([?&]({_SECRET_NAME)=)([^&\s]+)")
_COMMAND_LIMIT = 1024


def redact_command(command: str) -> str:
    command = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", command)
    command = _SECRET_ARGUMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", command)
    command = _SHORT_SECRET_ARGUMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED]", command)
    return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}[REDACTED]", command)[:_COMMAND_LIMIT]


def parse_process_stat(text: str) -> tuple[str, str, int, int, int, int, int, int]:
    """Parse /proc/<pid>/stat after locating its parenthesized, space-containing name."""
    left = text.find("(")
    right = text.rfind(")")
    if left < 0 or right <= left:
        raise ValueError("malformed process stat")
    name = text[left + 1:right]
    fields = text[right + 2:].split()
    if len(fields) < 22:
        raise ValueError("incomplete process stat")
    state = fields[0]
    ppid, utime, stime, threads, start_time, vsize, rss_pages = (
        int(fields[1]), int(fields[11]), int(fields[12]), int(fields[17]), int(fields[19]),
        int(fields[20]), int(fields[21]),
    )
    return name, state, ppid, utime + stime, threads, start_time, vsize, rss_pages


def read_process(pid: int, proc_root: Path) -> ProcessObservation:
    root = proc_root / str(pid)
    name, state, ppid, cpu_time, threads, start_time, vsize, rss_pages = parse_process_stat(
        (root / "stat").read_text(encoding="utf-8", errors="replace")
    )
    page_size = os.sysconf("SC_PAGE_SIZE")
    cmdline = (root / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    command = redact_command(cmdline) if cmdline else None
    try:
        executable = os.readlink(root / "exe")
    except (FileNotFoundError, PermissionError, OSError):
        executable = None
    return ProcessObservation(pid, ppid, name, state, rss_pages * page_size, vsize, threads, cpu_time,
                              start_time, command, executable)


def collect_processes(proc_root: Path = Path("/proc")) -> CollectionResult[tuple[ProcessObservation, ...]]:
    def operation() -> tuple[tuple[ProcessObservation, ...], tuple[str, ...]]:
        observations: list[ProcessObservation] = []
        warnings: list[str] = []
        for entry in proc_root.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                observations.append(read_process(int(entry.name), proc_root))
            except (FileNotFoundError, ProcessLookupError):
                warnings.append(f"process {entry.name} disappeared during collection")
            except PermissionError:
                warnings.append(f"process {entry.name} was inaccessible")
            except ValueError:
                warnings.append(f"process {entry.name} returned malformed data")
        return tuple(observations), tuple(warnings)

    result = collect(operation)
    if result.value is None:
        return result
    values, warnings = result.value
    status = CollectionStatus.PARTIAL if warnings else CollectionStatus.SUCCESS
    return CollectionResult(values, status, result.collected_at, result.duration_seconds,
                            "processes_partial" if warnings else None,
                            "some process observations were unavailable" if warnings else None, warnings)
