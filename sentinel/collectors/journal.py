"""Bounded, incremental journald collection through a fixed argv invocation."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from shutil import which

from sentinel.collectors.subprocesses import OutputLimitExceeded, run_bounded
from sentinel.models import EventObservation, JournalBatch
from sentinel.models.common import CollectionResult, CollectionStatus, utc_now
from sentinel.privacy import redact_sensitive_text

DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_LIMIT = 1_000
MAX_MESSAGE_BYTES = 4_096
MAX_FIELD_BYTES = 512
MAX_CURSOR_BYTES = 1_024
_TIMEOUT_SECONDS = 5
_OUTPUT_LIMIT = 2_000_000
_FIELDS = "__CURSOR,__REALTIME_TIMESTAMP,_BOOT_ID,SYSLOG_IDENTIFIER,PRIORITY,_SYSTEMD_UNIT,_SYSTEMD_USER_UNIT,_PID,_COMM,MESSAGE"


def collect_journal(after_cursor: str | None = None, *, limit: int = DEFAULT_EVENT_LIMIT) -> CollectionResult[JournalBatch]:
    """Read oldest retained events initially, then seek and verify the saved identity.

    Inclusive seeking detects journald's nearest-entry fallback after rotation. A
    missing checkpoint requires explicit recovery; it never silently resets history.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVENT_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_EVENT_LIMIT}")
    if after_cursor is not None:
        if not isinstance(after_cursor, str) or not after_cursor or "\x00" in after_cursor:
            raise ValueError("cursor must be a nonempty string without NUL")
        if len(after_cursor.encode("utf-8")) > MAX_CURSOR_BYTES:
            raise ValueError("cursor exceeds safety limit")
    timestamp = utc_now()
    started = time.monotonic()

    def failure(status: CollectionStatus, code: str, message: str) -> CollectionResult[JournalBatch]:
        return CollectionResult(None, status, timestamp, time.monotonic() - started, code, message)

    executable = which("journalctl")
    if executable is None:
        return failure(CollectionStatus.UNSUPPORTED, "journal_absent", "journalctl is not available")
    count = limit + 1 + (after_cursor is not None)
    command = [executable, "--output=json", "--all", "--no-pager", f"--output-fields={_FIELDS}", f"--lines=+{count}"]
    if after_cursor is not None:
        command.append(f"--cursor={after_cursor}")
    try:
        completed = run_bounded(command, timeout=_TIMEOUT_SECONDS, output_limit=_OUTPUT_LIMIT)
    except subprocess.TimeoutExpired:
        return failure(CollectionStatus.TRANSIENT_FAILURE, "journal_timeout", "journal did not respond in time")
    except OutputLimitExceeded:
        return failure(CollectionStatus.PARTIAL, "journal_output_limit", "journal output exceeded safety limit; checkpoint retained")
    except PermissionError:
        return failure(CollectionStatus.PERMISSION_DENIED, "journal_permission_denied", "journal access was denied")
    except FileNotFoundError:
        return failure(CollectionStatus.UNSUPPORTED, "journal_absent", "journalctl is not available")
    except OSError:
        return failure(CollectionStatus.TRANSIENT_FAILURE, "journal_execution_failed", "journal could not be executed")
    error = completed.stderr.decode("utf-8", errors="replace").lower()
    denied = any(fragment in error for fragment in ("permission denied", "not permitted", "not seeing messages", "insufficient permissions"))
    if completed.returncode != 0:
        if denied:
            return failure(CollectionStatus.PERMISSION_DENIED, "journal_permission_denied", "journal access was denied")
        if after_cursor is not None and "cursor" in error:
            return failure(CollectionStatus.TRANSIENT_FAILURE, "journal_cursor_invalidated", "stored journal cursor is unavailable; explicit recovery is required")
        return failure(CollectionStatus.TRANSIENT_FAILURE, "journal_unavailable", "journal access is unavailable")
    try:
        lines = completed.stdout.decode("utf-8").splitlines()
        if len(lines) > count:
            raise ValueError("journal exceeded requested event count")
        events = tuple(parse_journal_record(line) for line in lines if line.strip())
    except (ValueError, OverflowError, RecursionError):
        return failure(CollectionStatus.INVALID_DATA, "journal_invalid", "journal output was malformed")
    if after_cursor is not None:
        if not events or events[0].cursor != after_cursor:
            return failure(CollectionStatus.TRANSIENT_FAILURE, "journal_cursor_invalidated", "stored journal cursor is unavailable; explicit recovery is required")
        events = events[1:]
    if len({event.cursor for event in events}) != len(events) or any(event.cursor == after_cursor for event in events):
        return failure(CollectionStatus.INVALID_DATA, "journal_duplicate_cursor", "journal repeated an event identity")
    accepted = events[:limit]
    warnings = []
    if len(events) > limit:
        warnings.append("journal event limit reached; resume from stored cursor")
    if any(event.warnings for event in accepted):
        warnings.append("journal fields were truncated or transformed; see event warnings")
    if error.strip():
        warnings.append("journal access may be incomplete" if denied else "journal reported a diagnostic; collection may be incomplete")
    if denied and not accepted:
        return failure(CollectionStatus.PERMISSION_DENIED, "journal_permission_denied", "journal access was denied")
    batch = JournalBatch(accepted, accepted[-1].cursor if accepted else after_cursor)
    return CollectionResult(batch, CollectionStatus.PARTIAL if warnings else CollectionStatus.SUCCESS,
                            timestamp, time.monotonic() - started, "journal_partial" if warnings else None,
                            None, tuple(warnings))


def parse_journal_record(text: str) -> EventObservation:
    record = json.loads(text)
    if not isinstance(record, dict):
        raise ValueError("journal record must be an object")
    cursor = record.get("__CURSOR")
    if not isinstance(cursor, str) or not cursor or len(cursor.encode("utf-8")) > MAX_CURSOR_BYTES or "\x00" in cursor:
        raise ValueError("journal cursor is missing or invalid")
    raw_timestamp = _string(record.get("__REALTIME_TIMESTAMP"))
    if not raw_timestamp.isascii() or not raw_timestamp.isdecimal() or len(raw_timestamp) > 20:
        raise ValueError("invalid journal timestamp")
    timestamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=int(raw_timestamp))
    warnings: list[str] = []

    def field(name: str, value: object, bound: int = MAX_FIELD_BYTES) -> str:
        original = _string(value)
        redacted = (redact_sensitive_text(original, max_length=max(1, len(original) * 10))
                    if name in {"message", "source", "comm"} else original)
        if redacted != original:
            warnings.append(f"{name}_redacted")
        encoded = redacted.encode("utf-8")
        if len(encoded) > bound:
            warnings.append(f"{name}_truncated")
        return encoded[:bound].decode("utf-8", errors="ignore")

    message = field("message", record.get("MESSAGE"), MAX_MESSAGE_BYTES)
    source = field("source", record.get("SYSLOG_IDENTIFIER", "journal"))
    unit = field("unit", record.get("_SYSTEMD_UNIT") or record.get("_SYSTEMD_USER_UNIT")) or None
    comm = field("comm", record.get("_COMM")) or None
    boot_id = field("boot_id", record.get("_BOOT_ID")) or None
    return EventObservation(cursor, timestamp, source, _optional_int(record.get("PRIORITY"), maximum=7),
                            unit, _optional_int(record.get("_PID"), maximum=2**31 - 1), comm, message,
                            boot_id, tuple(warnings))


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    # journalctl JSON encodes binary fields as unsigned byte arrays.
    if isinstance(value, list) and all(type(item) is int and 0 <= item <= 255 for item in value):
        return bytes(value).decode("utf-8")
    raise ValueError("journal field must be text or bytes")


def _optional_int(value: object, *, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise ValueError("invalid journal integer")
    number = int(value)
    if number > maximum:
        raise ValueError("journal integer out of range")
    return number
