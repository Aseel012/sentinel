from pathlib import Path
import tempfile
import unittest

from sentinel.collectors.cpu import parse_cpu_stat
from sentinel.collectors.memory import parse_meminfo
from sentinel.collectors.network import parse_network_dev
from sentinel.collectors.processes import parse_process_stat, redact_command
from sentinel.models import CollectionStatus
from sentinel.models.common import CollectionResult
from sentinel.collectors.processes import collect_processes


class CollectorTests(unittest.TestCase):
    def test_meminfo_uses_explicit_kibibyte_conversion(self) -> None:
        observation = parse_meminfo("MemTotal: 10 kB\nMemAvailable: 5 kB\nMemFree: 1 kB\nCached: 2 kB\n")
        self.assertEqual(observation.total_bytes, 10 * 1024)
        self.assertEqual(observation.available_bytes, 5 * 1024)
        self.assertEqual(observation.cached_bytes, 2 * 1024)

    def test_meminfo_requires_available_not_just_free(self) -> None:
        with self.assertRaises(ValueError):
            parse_meminfo("MemTotal: 10 kB\nMemFree: 1 kB\n")

    def test_cpu_is_raw_cumulative_counters(self) -> None:
        observation = parse_cpu_stat("cpu  1 2 3 4 5 6 7 8 9 10\n")
        self.assertEqual(observation.total_ticks, 36)
        self.assertEqual(observation.idle_ticks, 4)

    def test_network_malformed_record_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_network_dev("header\nheader\neth0: 1 2\n")

    def test_process_stat_handles_spaces_in_comm(self) -> None:
        suffix = "S 12 0 0 0 0 0 0 0 0 0 13 17 0 0 0 0 4 0 99 4096 7"
        self.assertEqual(parse_process_stat(f"123 (two words) {suffix}"),
                         ("two words", "S", 12, 30, 4, 99, 4096, 7))

    def test_command_redaction_and_bound(self) -> None:
        command = redact_command("app --token=topsecret --password:pw --secret hunter2 -p short https://x/?api_key=query " + "x" * 2000)
        self.assertNotIn("topsecret", command)
        self.assertNotIn("password:pw", command)
        self.assertNotIn("hunter2", command)
        self.assertNotIn("short", command)
        self.assertNotIn("query", command)
        self.assertIn("[REDACTED]", command)
        self.assertEqual(len(command), 1024)

    def test_disappearing_process_is_partial_not_empty_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "123").mkdir()
            result = collect_processes(path)
        self.assertIs(result.status, CollectionStatus.PARTIAL)
        self.assertEqual(result.value, ())
        self.assertTrue(result.warnings)
