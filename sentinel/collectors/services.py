"""Optional systemd service collector using a fixed, bounded subprocess invocation."""

from __future__ import annotations

import subprocess
from shutil import which

from sentinel.models import ServiceObservation
from sentinel.models.common import CollectionResult, CollectionStatus, utc_now

_COMMAND = ("systemctl", "--user", "list-units", "--type=service", "--all", "--no-legend", "--no-pager",
            "--plain")
_TIMEOUT_SECONDS = 5
_OUTPUT_LIMIT = 1_000_000


def parse_services(text: str) -> tuple[ServiceObservation, ...]:
    services = []
    for line in text.splitlines():
        fields = line.split(maxsplit=4)
        if not fields:
            continue
        if len(fields) < 4:
            raise ValueError("malformed systemctl output")
        services.append(ServiceObservation(fields[0], fields[1], fields[2], fields[3]))
    return tuple(services)


def collect_services() -> CollectionResult[tuple[ServiceObservation, ...]]:
    timestamp = utc_now()
    if which("systemctl") is None:
        return CollectionResult(None, CollectionStatus.UNSUPPORTED, timestamp, 0.0, "systemd_absent",
                                "systemctl is not available")
    try:
        completed = subprocess.run(_COMMAND, shell=False, check=False, text=True, capture_output=True,
                                   timeout=_TIMEOUT_SECONDS, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    except subprocess.TimeoutExpired:
        return CollectionResult(None, CollectionStatus.TRANSIENT_FAILURE, timestamp, _TIMEOUT_SECONDS,
                                "systemctl_timeout", "systemd did not respond in time")
    if len(completed.stdout) > _OUTPUT_LIMIT:
        return CollectionResult(None, CollectionStatus.INVALID_DATA, timestamp, 0.0, "systemctl_output_limit",
                                "systemd output exceeded safety limit")
    if completed.returncode != 0:
        return CollectionResult(None, CollectionStatus.UNSUPPORTED, timestamp, 0.0, "systemd_unavailable",
                                "user systemd service manager is unavailable")
    try:
        value = parse_services(completed.stdout)
    except ValueError:
        return CollectionResult(None, CollectionStatus.INVALID_DATA, timestamp, 0.0, "systemctl_invalid",
                                "systemd output was malformed")
    return CollectionResult(value, CollectionStatus.SUCCESS, timestamp, 0.0)
