from datetime import UTC, datetime
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from sentinel.collectors.journal import JournalBatch, collect_journal, parse_journal_record
from sentinel.models import CollectionStatus
from sentinel.collectors.subprocesses import OutputLimitExceeded, run_bounded


def record(cursor: str = "cursor-1", message: str = "started") -> str:
    return json.dumps({"__CURSOR": cursor, "__REALTIME_TIMESTAMP": "1767225600123456", "MESSAGE": message,
                       "PRIORITY": "5", "_SYSTEMD_UNIT": "api.service", "_PID": "7", "_COMM": "api",
                       "_BOOT_ID": "boot-1"})


class JournalCollectorTests(unittest.TestCase):
    def test_parse_normalizes_timestamp_and_redacts_bounded_message(self) -> None:
        event = parse_journal_record(record(message="password=hunter2 " + "x" * 5000))
        self.assertEqual(event.timestamp, datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC))
        self.assertIn("password=[REDACTED]", event.message)
        self.assertLessEqual(len(event.message), 4096)

    def test_missing_required_cursor_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_journal_record(json.dumps({"__REALTIME_TIMESTAMP": "1"}))

    @patch("sentinel.collectors.journal.which", return_value=None)
    def test_absent_journal_is_not_an_empty_stream(self, _which) -> None:
        result = collect_journal()
        self.assertIsNone(result.value)
        self.assertEqual(result.status, CollectionStatus.UNSUPPORTED)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_incremental_batch_and_limit_preserve_resume_cursor(self, run, _which) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, (record("prior-cursor") + "\n" + record("cursor-1") + "\n" + record("cursor-2")).encode(), b"")
        result = collect_journal("prior-cursor", limit=1)
        self.assertEqual(result.status, CollectionStatus.PARTIAL)
        self.assertIsInstance(result.value, JournalBatch)
        self.assertEqual(result.value.next_cursor, "cursor-1")
        command = run.call_args.args[0]
        self.assertIn("--cursor=prior-cursor", command)
        self.assertIn("--lines=+3", command)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded", side_effect=subprocess.TimeoutExpired("journalctl", 5))
    def test_timeout_is_transient_failure(self, _run, _which) -> None:
        self.assertEqual(collect_journal().status, CollectionStatus.TRANSIENT_FAILURE)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_invalidated_cursor_does_not_look_like_an_empty_history(self, run, _which) -> None:
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"Failed to seek to cursor")
        result = collect_journal("old-cursor")
        self.assertEqual(result.status, CollectionStatus.TRANSIENT_FAILURE)
        self.assertEqual(result.error_code, "journal_cursor_invalidated")

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_pages_drain_burst_without_skipping_or_duplicating(self, run, _which) -> None:
        history = [record(f"cursor-{index}") for index in range(8)]

        def journal(command, **kwargs):
            count = int(next(arg.split("+")[1] for arg in command if arg.startswith("--lines=")))
            cursor = next((arg.split("=", 1)[1] for arg in command if arg.startswith("--cursor=")), None)
            start = int(cursor.rsplit("-", 1)[1]) if cursor else 0
            return subprocess.CompletedProcess(command, 0, "\n".join(history[start:start + count]).encode(), b"")

        run.side_effect = journal
        cursor = None
        seen = []
        for _ in range(5):
            result = collect_journal(cursor, limit=2)
            self.assertIsNotNone(result.value)
            seen.extend(event.cursor for event in result.value.events)
            cursor = result.value.next_cursor
        self.assertEqual(seen, [f"cursor-{index}" for index in range(8)])
        self.assertEqual(result.value.events, ())
        self.assertEqual(cursor, "cursor-7")
        self.assertEqual(result.status, CollectionStatus.SUCCESS)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_rotated_checkpoint_nearest_fallback_is_not_accepted(self, run, _which) -> None:
        for stdout in (b"", record("nearest-survivor").encode()):
            with self.subTest(stdout=stdout):
                run.return_value = subprocess.CompletedProcess([], 0, stdout, b"")
                result = collect_journal("lost-checkpoint")
                self.assertEqual(result.error_code, "journal_cursor_invalidated")
                self.assertIsNone(result.value)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_empty_first_journal_is_success(self, run, _which) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, b"", b"")
        result = collect_journal()
        self.assertEqual(result.status, CollectionStatus.SUCCESS)
        self.assertEqual(result.value, JournalBatch((), None))

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_message_and_metadata_bounds_are_explicit(self, run, _which) -> None:
        payload = json.loads(record(message="🙂" * 4096))
        payload["SYSLOG_IDENTIFIER"] = "a" * 600
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
        result = collect_journal()
        event = result.value.events[0]
        self.assertEqual(result.status, CollectionStatus.PARTIAL)
        self.assertLessEqual(len(event.message.encode()), 4096)
        self.assertEqual(len(event.source.encode()), 512)
        self.assertIn("message_truncated", event.warnings)
        self.assertIn("source_truncated", event.warnings)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_bad_input_never_advances_checkpoint(self, run, _which) -> None:
        base = json.loads(record())
        payloads = [[], None, 3, {**base, "__CURSOR": {}}, {**base, "PRIORITY": []},
                    {**base, "PRIORITY": "9"}, {**base, "__REALTIME_TIMESTAMP": "9" * 20},
                    {**base, "MESSAGE": {"bad": "shape"}}, {**base, "__CURSOR": "x" * 1025}]
        for payload in payloads:
            with self.subTest(payload=payload):
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
                result = collect_journal()
                self.assertEqual(result.status, CollectionStatus.INVALID_DATA)
                self.assertIsNone(result.value)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_failures_and_partial_access(self, run, _which) -> None:
        for error, status in ((PermissionError(), CollectionStatus.PERMISSION_DENIED),
                              (FileNotFoundError(), CollectionStatus.UNSUPPORTED),
                              (OSError(), CollectionStatus.TRANSIENT_FAILURE),
                              (OutputLimitExceeded(), CollectionStatus.PARTIAL)):
            with self.subTest(error=error):
                run.side_effect = error
                self.assertEqual(collect_journal().status, status)
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"Permission denied")
        self.assertEqual(collect_journal().status, CollectionStatus.PERMISSION_DENIED)
        run.return_value = subprocess.CompletedProcess([], 0, record().encode(), b"You are currently not seeing messages from other users")
        self.assertEqual(collect_journal().status, CollectionStatus.PARTIAL)

    def test_binary_message_and_user_unit(self) -> None:
        payload = json.loads(record())
        payload["MESSAGE"] = [104, 105]
        del payload["_SYSTEMD_UNIT"]
        payload["_SYSTEMD_USER_UNIT"] = "user.service"
        event = parse_journal_record(json.dumps(payload))
        self.assertEqual(event.message, "hi")
        self.assertEqual(event.unit, "user.service")

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_duplicate_cursor_is_invalid_even_with_identical_facts(self, run, _which) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, (record() + "\n" + record()).encode(), b"")
        result = collect_journal()
        self.assertEqual(result.error_code, "journal_duplicate_cursor")
        self.assertIsNone(result.value)

    def test_equal_timestamp_and_message_do_not_define_identity(self) -> None:
        first = parse_journal_record(record("first"))
        second = parse_journal_record(record("second"))
        self.assertEqual(first.timestamp, second.timestamp)
        self.assertEqual(first.message, second.message)
        self.assertNotEqual(first.cursor, second.cursor)

    @patch("sentinel.collectors.journal.which", return_value="/usr/bin/journalctl")
    @patch("sentinel.collectors.journal.run_bounded")
    def test_boot_change_preserves_context_when_checkpoint_is_still_retained(self, run, _which) -> None:
        current = json.loads(record("new-boot-event"))
        current["_BOOT_ID"] = "boot-2"
        run.return_value = subprocess.CompletedProcess([], 0,
            (record("old-boot-event") + "\n" + json.dumps(current)).encode(), b"")
        result = collect_journal("old-boot-event")
        self.assertEqual(result.status, CollectionStatus.SUCCESS)
        self.assertEqual(result.value.next_cursor, "new-boot-event")
        self.assertEqual(result.value.events[0].boot_id, "boot-2")

    def test_limit_and_cursor_validation(self) -> None:
        for limit in (0, -1, True, 1001, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                collect_journal(limit=limit)
        for cursor in ("", "x\x00", "x" * 1025):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                collect_journal(cursor)


class BoundedSubprocessTests(unittest.TestCase):
    def test_drains_both_pipes(self) -> None:
        result = run_bounded([sys.executable, "-c", "import os; os.write(1,b'out'); os.write(2,b'err')"],
                             timeout=2, output_limit=6)
        self.assertEqual((result.stdout, result.stderr, result.returncode), (b"out", b"err", 0))

    def test_combined_budget_stops_stderr_flood(self) -> None:
        with self.assertRaises(OutputLimitExceeded):
            run_bounded([sys.executable, "-c", "import os; os.write(1,b'abcd'); os.write(2,b'efgh')"],
                        timeout=2, output_limit=7)

    def test_timeout_reaps_child(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05, output_limit=100)
