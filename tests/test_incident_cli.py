import contextlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest

from sentinel.main import main
from sentinel.models import EvidenceFact
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import IncidentRepository
from sentinel.storage.migrations import initialize_schema
from tests.test_diagnosis import candidate


NOW = datetime(2026, 5, 1, tzinfo=UTC)


class IncidentCLITests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sentinel.db"
        self.older = candidate(at=NOW)
        self.newer = candidate(at=NOW + timedelta(minutes=1))
        with database_connection(self.path) as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            for item in (self.older, self.newer):
                repository.reconcile(
                    (item,), resolved_subjects=(), observed_at=item.last_observed_at,
                    resolution_authoritative=False,
                )

    def invoke(self, arguments: list[str]) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(arguments)
        return code, output.getvalue()

    def test_list_text_and_json_are_bounded_and_ordered(self) -> None:
        code, text = self.invoke(["incidents", "--database", str(self.path), "--limit", "1"])
        self.assertEqual(code, 0)
        self.assertIn(self.newer.incident_id, text)
        self.assertNotIn(self.older.incident_id, text)
        code, output = self.invoke(["incidents", "--database", str(self.path), "--json"])
        response = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(response["schema_version"], "1")
        self.assertEqual(response["count"], 2)
        self.assertEqual(response["query"], {"limit": 100, "limit_reached": False})
        self.assertEqual([item["incident_id"] for item in response["incidents"]],
                         [self.newer.incident_id, self.older.incident_id])

    def test_single_json_contains_lifecycle_evidence_facts_and_reused_diagnosis(self) -> None:
        code, output = self.invoke([
            "incident", self.newer.incident_id, "--database", str(self.path), "--json",
        ])
        response = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(response["incident"]["state"], "active")
        self.assertIsNone(response["incident"]["resolved_at"])
        self.assertEqual(response["diagnosis"]["incident_id"], self.newer.incident_id)
        self.assertIn("service_change_anchor",
                      {item["relation"] for item in response["evidence"]})
        self.assertIn("service_failed", response["facts"]["established"])
        self.assertIn("current_system_state", response["facts"]["not_established"])
        self.assertEqual(response["historical_related_incident_ids"], [self.older.incident_id])
        _, diagnosis_output = self.invoke([
            "diagnose", self.newer.incident_id, "--database", str(self.path), "--json",
        ])
        self.assertEqual(response["diagnosis"], json.loads(diagnosis_output)["diagnosis"])
        ordering = [(item["observed_at"], item["kind"], item["evidence_id"])
                    for item in response["evidence"]]
        self.assertEqual(ordering, sorted(ordering))

    def test_unknown_evidence_and_quality_limitations_remain_visible(self) -> None:
        evidence = tuple(replace(item, fact=EvidenceFact.UNKNOWN,
                                 quality_limitations=("source_partial",))
                         for item in self.older.evidence)
        unknown = replace(self.older, evidence=evidence,
                          quality_limitations=("incident_evidence_incomplete",))
        path = self.path.with_name("unknown.db")
        with database_connection(path) as connection:
            initialize_schema(connection)
            IncidentRepository(connection).reconcile(
                (unknown,), resolved_subjects=(), observed_at=unknown.last_observed_at,
                resolution_authoritative=False,
            )
        _, output = self.invoke(["incident", unknown.incident_id,
                                 "--database", str(path), "--json"])
        response = json.loads(output)
        self.assertEqual(response["facts"]["established"], [])
        self.assertEqual(len(response["facts"]["unknown_evidence_ids"]), 2)
        self.assertIn("incident_evidence_incomplete", response["limitations"]["incident"])
        self.assertIn("source_partial", response["evidence"][0]["quality_limitations"])

    def test_text_is_deterministic_and_excludes_sensitive_raw_data(self) -> None:
        arguments = ["incident", self.older.incident_id, "--database", str(self.path)]
        first = self.invoke(arguments)
        second = self.invoke(arguments)
        self.assertEqual(first, second)
        self.assertIn("does not establish current system state", first[1])
        self.assertIn("exact root cause", first[1])
        self.assertNotIn("raw private body", first[1])
        self.assertNotIn("/bin/api", first[1])

    def test_missing_and_invalid_ids_have_stable_visible_failures(self) -> None:
        code, output = self.invoke([
            "incident", "f" * 64, "--database", str(self.path), "--json",
        ])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["error"]["code"], "incident_not_found")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["incident", "invalid", "--database", str(self.path)])
        self.assertEqual(raised.exception.code, 2)
