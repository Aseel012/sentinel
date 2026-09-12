import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from sentinel.main import main
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import IncidentRepository
from sentinel.storage.migrations import initialize_schema
from tests.test_diagnosis import candidate


class DiagnosisCLITests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sentinel.db"
        self.incident = candidate()
        with database_connection(self.path) as connection:
            initialize_schema(connection)
            IncidentRepository(connection).reconcile(
                (self.incident,), resolved_subjects=(),
                observed_at=self.incident.last_observed_at,
                resolution_authoritative=False,
            )

    def test_json_diagnosis_has_structured_provenance_and_no_raw_message(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["diagnose", self.incident.incident_id, "--database", str(self.path), "--json"])
        self.assertEqual(code, 0)
        response = json.loads(output.getvalue())
        self.assertEqual(response["diagnosis"]["state"], "supported")
        self.assertEqual(response["diagnosis"]["provenance"][0].keys(),
                         {"kind", "evidence_id", "observed_at", "relation", "fact",
                          "quality_limitations"})
        self.assertNotIn("raw private body", output.getvalue())

    def test_text_diagnosis_and_missing_incident_have_stable_exit_codes(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["diagnose", self.incident.incident_id,
                                   "--database", str(self.path)]), 0)
        self.assertIn("The exact root cause", output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["diagnose", "f" * 64, "--database", str(self.path), "--json"]), 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "incident_not_found")
