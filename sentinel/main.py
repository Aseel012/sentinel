"""Small Phase-1 CLI; it invokes application services rather than inspecting Linux itself."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sentinel import __version__
from sentinel.application.snapshot_service import SnapshotService
from sentinel.cli.formatters import format_snapshot
from sentinel.cli.json_output import status_response
from sentinel.platform.capabilities import detect_capabilities
from sentinel.platform.detection import detect_platform


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel", description="Local-first Linux system observation")
    parser.add_argument("--version", action="version", version=f"sentinel {__version__}")
    sub = parser.add_subparsers(dest="command")
    for name in ("status", "doctor"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "status"
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
