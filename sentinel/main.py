"""Sentinel CLI over collection, diagnosis, and bounded historical inspection."""

from __future__ import annotations

import argparse
import json
import signal
from dataclasses import asdict
from pathlib import Path

from sentinel import __version__
from sentinel.application.snapshot_service import SnapshotService
from sentinel.application.diagnosis_service import DiagnosisService
from sentinel.application.inspection_service import IncidentInspectionService
from sentinel.application.event_service import JournalEventService
from sentinel.application.runtime import ContinuousObservationRuntime
from sentinel.application.runtime_models import (
    MAX_RUNTIME_SNAPSHOT_LIMIT,
    MAX_RUNTIME_CYCLES,
    MAX_RUNTIME_INTERVAL_SECONDS,
    MIN_RUNTIME_INTERVAL_SECONDS,
    RuntimeConfig,
)
from sentinel.collectors.journal import DEFAULT_EVENT_LIMIT, MAX_EVENT_LIMIT, collect_journal
from sentinel.cli.diagnosis_output import DIAGNOSIS_SCHEMA_VERSION, diagnosis_response, format_diagnosis
from sentinel.cli.formatters import format_snapshot
from sentinel.cli.incident_output import (
    INCIDENT_INSPECTION_SCHEMA_VERSION,
    format_incident_list,
    format_inspection,
    incident_list_response,
    inspection_response,
)
from sentinel.cli.json_output import status_response
from sentinel.cli.runtime_output import format_runtime_cycle, runtime_cycle_response
from sentinel.models import IncidentState
from sentinel.storage.incidents import DEFAULT_INCIDENT_LIMIT, MAX_INCIDENT_LIMIT
from sentinel.platform.capabilities import detect_capabilities
from sentinel.platform.detection import detect_platform


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel", description="Local-first Linux system observation")
    parser.add_argument("--version", action="version", version=f"sentinel {__version__}")
    sub = parser.add_subparsers(dest="command")
    for name in ("status", "doctor"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true", dest="as_json")
    diagnose = sub.add_parser("diagnose")
    diagnose.add_argument("incident_id", type=_incident_id)
    diagnose.add_argument("--database", type=Path)
    diagnose.add_argument("--json", action="store_true", dest="as_json")
    incidents = sub.add_parser("incidents")
    incidents.add_argument("--database", type=Path)
    incidents.add_argument("--limit", type=_incident_limit, default=DEFAULT_INCIDENT_LIMIT)
    incidents.add_argument("--state", type=IncidentState, choices=tuple(IncidentState))
    incidents.add_argument("--json", action="store_true", dest="as_json")
    incident = sub.add_parser("incident")
    incident.add_argument("incident_id", type=_incident_id)
    incident.add_argument("--database", type=Path)
    incident.add_argument("--json", action="store_true", dest="as_json")
    run = sub.add_parser("run")
    run.add_argument("--database", type=Path)
    run.add_argument("--interval", type=_runtime_interval, default=60.0)
    run.add_argument("--cycles", type=_runtime_cycles)
    run.add_argument("--event-limit", type=_event_limit, default=DEFAULT_EVENT_LIMIT)
    run.add_argument("--snapshot-limit", type=_snapshot_limit, default=10_000)
    run.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _incident_id(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise argparse.ArgumentTypeError("incident-id must be 64 lowercase hexadecimal characters")
    return value


def _incident_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= limit <= MAX_INCIDENT_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_INCIDENT_LIMIT}")
    return limit


def _runtime_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number") from exc
    if not MIN_RUNTIME_INTERVAL_SECONDS <= interval <= MAX_RUNTIME_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"interval must be between {MIN_RUNTIME_INTERVAL_SECONDS} "
            f"and {MAX_RUNTIME_INTERVAL_SECONDS} seconds"
        )
    return interval


def _runtime_cycles(value: str) -> int:
    try:
        cycles = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("cycles must be an integer") from exc
    if not 1 <= cycles <= MAX_RUNTIME_CYCLES:
        raise argparse.ArgumentTypeError(f"cycles must be between 1 and {MAX_RUNTIME_CYCLES}")
    return cycles


def _event_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("event-limit must be an integer") from exc
    if not 1 <= limit <= MAX_EVENT_LIMIT:
        raise argparse.ArgumentTypeError(f"event-limit must be between 1 and {MAX_EVENT_LIMIT}")
    return limit


def _snapshot_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("snapshot-limit must be an integer") from exc
    if not 1 <= limit <= MAX_RUNTIME_SNAPSHOT_LIMIT:
        raise argparse.ArgumentTypeError(
            f"snapshot-limit must be between 1 and {MAX_RUNTIME_SNAPSHOT_LIMIT}"
        )
    return limit


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "status"
    if command == "diagnose":
        diagnosis = DiagnosisService(args.database).diagnose(args.incident_id)
        if diagnosis is None:
            if args.as_json:
                print(json.dumps({"schema_version": DIAGNOSIS_SCHEMA_VERSION,
                                  "error": {"code": "incident_not_found",
                                            "incident_id": args.incident_id}}, separators=(",", ":")))
            else:
                print(f"Incident not found: {args.incident_id}")
            return 1
        if args.as_json:
            print(json.dumps(diagnosis_response(diagnosis), separators=(",", ":")))
        else:
            print(format_diagnosis(diagnosis))
        return 0
    if command == "incidents":
        incidents = IncidentInspectionService(args.database).list_incidents(
            limit=args.limit,
            state=args.state,
        )
        if args.as_json:
            print(json.dumps(incident_list_response(incidents, query_limit=args.limit),
                             separators=(",", ":")))
        else:
            print(format_incident_list(incidents, query_limit=args.limit))
        return 0
    if command == "incident":
        inspection = IncidentInspectionService(args.database).inspect(args.incident_id)
        if inspection is None:
            if args.as_json:
                print(json.dumps({
                    "schema_version": INCIDENT_INSPECTION_SCHEMA_VERSION,
                    "error": {"code": "incident_not_found", "incident_id": args.incident_id},
                }, separators=(",", ":")))
            else:
                print(f"Incident not found: {args.incident_id}")
            return 1
        if args.as_json:
            print(json.dumps(inspection_response(inspection), separators=(",", ":")))
        else:
            print(format_inspection(inspection))
        return 0
    if command == "run":
        def collect_events(cursor):
            return collect_journal(cursor, limit=args.event_limit)

        def emit(cycle) -> None:
            if args.as_json:
                print(json.dumps(runtime_cycle_response(cycle), separators=(",", ":")), flush=True)
            else:
                print(format_runtime_cycle(cycle), flush=True)

        runtime = ContinuousObservationRuntime(
            args.database,
            RuntimeConfig(
                interval_seconds=args.interval,
                event_query_limit=args.event_limit,
                max_snapshots=args.snapshot_limit,
            ),
            event_recorder=JournalEventService(args.database, collect_events),
            cycle_sink=emit,
        )
        signals = (signal.SIGINT, signal.SIGTERM)
        previous_handlers = tuple((item, signal.getsignal(item)) for item in signals)

        def request_stop(_signum, _frame) -> None:
            runtime.request_stop()

        try:
            for item, _ in previous_handlers:
                signal.signal(item, request_stop)
            runtime.run(max_cycles=args.cycles)
        finally:
            for item, handler in previous_handlers:
                signal.signal(item, handler)
        return 0
    info = detect_platform()
    if command == "doctor":
        capabilities = detect_capabilities(info)
        if args.as_json:
            print(json.dumps({"schema_version": "1", "platform": asdict(info),
                              "capabilities": [asdict(item) for item in capabilities]}, default=str))
        else:
            print("Sentinel Doctor")
            for capability in capabilities:
                print(f"{capability.name}: {capability.state.value}")
        return 0 if info.is_linux and info.proc_available else 2
    collection = SnapshotService().collect()
    if getattr(args, "as_json", False):
        print(json.dumps(status_response(collection), separators=(",", ":")))
    else:
        print(format_snapshot(collection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
