"""Optional systemd service collector using a fixed, bounded subprocess invocation."""

from __future__ import annotations

import subprocess
from shutil import which

from sentinel.collectors.subprocesses import OutputLimitExceeded, run_bounded
from sentinel.models import ServiceObservation
from sentinel.models.common import CollectionResult, CollectionStatus, utc_now

_COMMAND = ("systemctl", "--user", "list-units", "--type=service", "--all", "--no-legend", "--no-pager",
            "--plain")
_TIMEOUT_SECONDS = 5
_OUTPUT_LIMIT = 1_000_000


def parse_services(text: str) -> tuple[ServiceObservation, ...]:
    services = []
    names: set[str] = set()
    for line in text.splitlines():
        fields = line.split(maxsplit=4)
        if not fields:
            continue
        if len(fields) < 4:
            raise ValueError("malformed systemctl output")
        if (not fields[0].endswith(".service") or fields[0] == ".service"
                or fields[0] in names or any(ord(char) < 32 for field in fields[:4] for char in field)):
            raise ValueError("invalid or duplicate systemctl unit")
        names.add(fields[0])
        services.append(ServiceObservation(fields[0], fields[1], fields[2], fields[3]))
    return tuple(services)


def collect_services() -> CollectionResult[tuple[ServiceObservation, ...]]:
    timestamp = utc_now()
    if which("systemctl") is None:
        return CollectionResult(None, CollectionStatus.UNSUPPORTED, timestamp, 0.0, "systemd_absent",
                                "systemctl is not available")
    try:
        completed = run_bounded(list(_COMMAND), timeout=_TIMEOUT_SECONDS, output_limit=_OUTPUT_LIMIT)
        stdout = completed.stdout.decode("utf-8")
    except subprocess.TimeoutExpired:
        return CollectionResult(None, CollectionStatus.TRANSIENT_FAILURE, timestamp, _TIMEOUT_SECONDS,
                                "systemctl_timeout", "systemd did not respond in time")
    except PermissionError:
        return CollectionResult(None, CollectionStatus.PERMISSION_DENIED, timestamp, 0.0,
                                "systemctl_permission_denied", "systemctl execution was denied")
    except OSError:
        return CollectionResult(None, CollectionStatus.TRANSIENT_FAILURE, timestamp, 0.0,
                                "systemctl_execution_failed", "systemctl could not be executed")
    except UnicodeError:
        return CollectionResult(None, CollectionStatus.INVALID_DATA, timestamp, 0.0,
                                "systemctl_encoding", "systemd output was not valid text")
    except OutputLimitExceeded:
        return CollectionResult(None, CollectionStatus.INVALID_DATA, timestamp, 0.0, "systemctl_output_limit",
                                "systemd output exceeded safety limit")
    if completed.returncode != 0:
        if any(marker in completed.stderr.lower() for marker in (b"permission denied", b"access denied")):
            return CollectionResult(None, CollectionStatus.PERMISSION_DENIED, timestamp, 0.0,
                                    "systemd_permission_denied", "access to the user service manager was denied")
        return CollectionResult(None, CollectionStatus.UNSUPPORTED, timestamp, 0.0, "systemd_unavailable",
                                "user systemd service manager is unavailable")
    try:
        value = parse_services(stdout)
    except ValueError:
        return CollectionResult(None, CollectionStatus.INVALID_DATA, timestamp, 0.0, "systemctl_invalid",
                                "systemd output was malformed")
    return CollectionResult(value, CollectionStatus.SUCCESS, timestamp, 0.0)
