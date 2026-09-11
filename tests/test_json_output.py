import contextlib
from datetime import UTC, datetime
import io
import json
import unittest
from unittest.mock import patch

from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.cli.json_output import status_response
from sentinel.main import main
from sentinel.models import (CPUObservation, CollectionResult, CollectionStatus, MemoryObservation,
                             ProcessObservation, SystemObservation, SystemSnapshot)


def fake_collection(status: CollectionStatus = CollectionStatus.SUCCESS) -> SnapshotCollection:
    timestamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    system = SystemObservation("host", "Linux", "6.0", "x86_64", 12.5)
    memory = MemoryObservation(100, 60, 20, None, 30, 0, 0)
    cpu = CPUObservation(1, 2, 3, 4, 5, 6, 7, 8)
    process = ProcessObservation(42, 1, "worker", "S", 10, 20, 2, 3, 99,
                                 "worker --token=[REDACTED]", "/usr/bin/worker")
    result_value = memory if status is CollectionStatus.SUCCESS else None
    memory_result = CollectionResult(result_value, status, timestamp, 0.25,
                                     "source_missing" if result_value is None else None,
                                     "source unavailable" if result_value is None else None,
                                     ("memory source unavailable",) if result_value is None else ())
    snapshot = SystemSnapshot(timestamp, system, memory if result_value else None, cpu, (process,), (), (), (),
                              (("system", "success"), ("memory", status.value)),
                              ("memory source unavailable",) if result_value is None else ())
    system_result = CollectionResult(system, CollectionStatus.SUCCESS, timestamp, 0.1)
    return SnapshotCollection(snapshot, (("system", system_result), ("memory", memory_result)))


class JsonOutputTests(unittest.TestCase):
    def test_response_is_valid_json_with_stable_schema_and_observations(self) -> None:
        response = status_response(fake_collection())
        decoded = json.loads(json.dumps(response))
        self.assertEqual(decoded["schema_version"], "1")
        self.assertEqual(decoded["snapshot"]["timestamp"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(decoded["snapshot"]["processes"][0]["lifetime_id"], "42:99")
        self.assertEqual(decoded["collectors"]["memory"]["status"], "success")
        self.assertEqual(decoded["collectors"]["memory"]["warnings"], [])
        self.assertNotIn("value", decoded["collectors"]["memory"])

    def test_degraded_collection_remains_valid_and_preserves_error_contract(self) -> None:
        decoded = json.loads(json.dumps(status_response(fake_collection(CollectionStatus.TRANSIENT_FAILURE))))
        self.assertIsNone(decoded["snapshot"]["memory"])
        self.assertEqual(decoded["collectors"]["memory"]["status"], "transient_failure")
        self.assertEqual(decoded["collectors"]["memory"]["error_code"], "source_missing")
        self.assertEqual(decoded["collectors"]["memory"]["warnings"], ["memory source unavailable"])
        self.assertEqual(decoded["snapshot"]["warnings"], ["memory source unavailable"])

    def test_json_cli_stdout_contains_only_one_json_document(self) -> None:
        output = io.StringIO()
        with patch("sentinel.main.SnapshotService") as service, contextlib.redirect_stdout(output):
            service.return_value.collect.return_value = fake_collection()
            self.assertEqual(main(["status", "--json"]), 0)
        decoded = json.loads(output.getvalue())
        self.assertEqual(decoded["schema_version"], "1")
        self.assertNotIn("Traceback", output.getvalue())

    def test_serializer_is_explicit_not_generic_dataclass_dumping(self) -> None:
        response = status_response(fake_collection())
        self.assertNotIn("total_ticks", response["snapshot"]["cpu"])
        self.assertNotIn("__dataclass_fields__", json.dumps(response))
