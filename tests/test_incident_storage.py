from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.models import (
    EvidenceFact, EvidenceKind, EvidenceReference, EvidenceRelation, IncidentCandidate, IncidentState,
    SubjectType,
)
from sentinel.models.incidents import MAX_EVIDENCE_PER_INCIDENT
from sentinel.storage.database import database_connection
from sentinel.storage.incidents import (
    EVIDENCE_LIMIT_REACHED, IncidentRepository, IncidentStorageError,
)
from sentinel.storage.migrations import apply_migrations, initialize_schema, installed_schema_version
from sentinel.storage.retention import delete_resolved_incidents_before
from sentinel.storage.schema import SCHEMA_VERSION


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
SUBJECT = (SubjectType.SERVICE, "api.service")


def identity(anchor: str, subject_id: str = SUBJECT[1]) -> str:
    return hashlib.sha256(f"service-incident-v1\0{subject_id}\0{anchor}".encode()).hexdigest()


def evidence(
    kind: EvidenceKind,
    evidence_id: str,
    observed_at: datetime,
    *,
    subject_id: str = SUBJECT[1],
    limitations: tuple[str, ...] = (),
) -> EvidenceReference:
    relations = {
        EvidenceKind.SERVICE_CHANGE: EvidenceRelation.SERVICE_CHANGE_ANCHOR,
        EvidenceKind.PROCESS_CHANGE: EvidenceRelation.EXPLICIT_PROCESS_ASSOCIATION,
        EvidenceKind.JOURNAL_EVENT: EvidenceRelation.SERVICE_UNIT_MATCH,
    }
    return EvidenceReference(kind, evidence_id, observed_at, SubjectType.SERVICE, subject_id,
                             relations[kind], f"structured {kind.value}", limitations)


def candidate(
    anchor: str = "service-change:1",
    *,
    subject_id: str = SUBJECT[1],
    last_observed_at: datetime = NOW + timedelta(seconds=1),
    extra_evidence: tuple[EvidenceReference, ...] = (),
    limitations: tuple[str, ...] = (),
) -> IncidentCandidate:
    anchor_evidence = evidence(EvidenceKind.SERVICE_CHANGE, anchor, NOW, subject_id=subject_id)
    default_related = evidence(EvidenceKind.JOURNAL_EVENT, "cursor:1", NOW + timedelta(seconds=1),
                               subject_id=subject_id)
    related = extra_evidence or (default_related,)
    return IncidentCandidate(identity(anchor, subject_id), "service-failure-v1", SubjectType.SERVICE,
                             subject_id, IncidentState.ACTIVE, NOW, last_observed_at, None,
                             (anchor_evidence, *related), limitations)


class IncidentStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sentinel.db"

    def connection(self):
        return database_connection(self.path)

    def test_v3_migration_preserves_history_and_adds_normalized_incidents(self) -> None:
        with self.connection() as connection:
            self.assertEqual(apply_migrations(connection, target_version=3), 3)
            connection.execute("INSERT INTO snapshots(observed_at) VALUES (?)", (NOW.isoformat(),))
            connection.execute(
                "INSERT INTO events(cursor, observed_at, source, message, warnings) VALUES (?, ?, ?, ?, ?)",
                ("cursor", NOW.isoformat(timespec="microseconds"), "journal", "kept", "[]"),
            )
            connection.commit()
            self.assertEqual(initialize_schema(connection), SCHEMA_VERSION)
            self.assertEqual(installed_schema_version(connection), SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT message FROM events").fetchone()[0], "kept")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertTrue({"incidents", "incident_evidence", "incident_limitations",
                             "incident_evidence_limitations"}.issubset(tables))

    def test_round_trip_is_idempotent_and_keeps_structured_evidence(self) -> None:
        item = candidate(limitations=("journal_partial",))
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            first = repository.reconcile((item,), resolved_subjects=(), observed_at=item.last_observed_at,
                                         resolution_authoritative=False)
            second = repository.reconcile((item, item), resolved_subjects=(),
                                          observed_at=item.last_observed_at,
                                          resolution_authoritative=False)
            self.assertEqual(first.candidate_ids, (item.incident_id,))
            self.assertEqual(second.candidate_ids, (item.incident_id,))
            self.assertEqual(repository.get(item.incident_id), item)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM incident_evidence").fetchone()[0], 2)
            stored_columns = {row[1] for row in connection.execute("PRAGMA table_info(incident_evidence)")}
            self.assertNotIn("message", stored_columns)

    def test_repeated_sampling_merges_evidence_and_advances_observed_time(self) -> None:
        first = candidate()
        second_time = NOW + timedelta(seconds=3)
        second = candidate(
            last_observed_at=second_time,
            extra_evidence=(evidence(EvidenceKind.JOURNAL_EVENT, "cursor:2", second_time),),
        )
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((first,), resolved_subjects=(), observed_at=first.last_observed_at,
                                 resolution_authoritative=False)
            repository.reconcile((second,), resolved_subjects=(), observed_at=second_time,
                                 resolution_authoritative=False)
            stored = repository.get(first.incident_id)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.last_observed_at, second_time)
            self.assertEqual(tuple(item.evidence_id for item in stored.evidence),
                             ("service-change:1", "cursor:1", "cursor:2"))

    def test_reprocessing_enriches_legacy_fact_and_accumulates_quality(self) -> None:
        original = candidate(limitations=())
        old_event = replace(original.evidence[1], quality_limitations=("first_limit",))
        legacy = replace(original, evidence=(original.evidence[0], old_event))
        typed_event = replace(old_event, fact=EvidenceFact.JOURNAL_UNIT_EVENT,
                              quality_limitations=("second_limit",))
        typed = replace(original, evidence=(original.evidence[0], typed_event))
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((legacy,), resolved_subjects=(), observed_at=NOW,
                                  resolution_authoritative=False)
            repository.reconcile((typed,), resolved_subjects=(), observed_at=NOW,
                                  resolution_authoritative=False)
            stored = repository.get(original.incident_id)
            self.assertIsNotNone(stored)
            event_fact = next(item for item in stored.evidence
                              if item.kind is EvidenceKind.JOURNAL_EVENT)
            self.assertEqual(event_fact.fact, EvidenceFact.JOURNAL_UNIT_EVENT)
            self.assertEqual(event_fact.quality_limitations, ("first_limit", "second_limit"))

    def test_only_authoritative_explicit_subject_resolution_changes_state(self) -> None:
        item = candidate()
        resolved_at = NOW + timedelta(seconds=5)
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((item,), resolved_subjects=(), observed_at=item.last_observed_at,
                                 resolution_authoritative=False)
            repository.reconcile((), resolved_subjects=(SUBJECT,), observed_at=resolved_at,
                                 resolution_authoritative=False)
            self.assertEqual(repository.get(item.incident_id).state, IncidentState.ACTIVE)  # type: ignore[union-attr]
            result = repository.reconcile((), resolved_subjects=(SUBJECT,), observed_at=resolved_at,
                                          resolution_authoritative=True)
            stored = repository.get(item.incident_id)
            self.assertEqual(result.resolved_ids, (item.incident_id,))
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual((stored.state, stored.last_observed_at, stored.resolved_at),
                             (IncidentState.RESOLVED, resolved_at, resolved_at))
            repository.reconcile((item,), resolved_subjects=(), observed_at=resolved_at,
                                 resolution_authoritative=False)
            self.assertEqual(repository.get(item.incident_id).state, IncidentState.RESOLVED)  # type: ignore[union-attr]

    def test_current_candidate_wins_over_resolution_for_same_subject(self) -> None:
        item = candidate()
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            result = repository.reconcile((item,), resolved_subjects=(SUBJECT,),
                                          observed_at=item.last_observed_at,
                                          resolution_authoritative=True)
            self.assertEqual(result.resolved_ids, ())
            self.assertEqual(repository.get(item.incident_id).state, IncidentState.ACTIVE)  # type: ignore[union-attr]

    def test_identity_collision_rolls_back_resolution_and_candidate_changes(self) -> None:
        item = candidate("anchor:a")
        inserted_before_failure = candidate("1", subject_id="worker.service")
        collision = replace(candidate("anchor:a", subject_id="other.service"),
                            incident_id=item.incident_id)
        self.assertLess(inserted_before_failure.incident_id, collision.incident_id)
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((item,), resolved_subjects=(), observed_at=item.last_observed_at,
                                 resolution_authoritative=False)
            with self.assertRaises(IncidentStorageError):
                repository.reconcile((collision, inserted_before_failure), resolved_subjects=(SUBJECT,),
                                     observed_at=NOW + timedelta(seconds=5),
                                     resolution_authoritative=True)
            self.assertEqual(repository.get(item.incident_id), item)
            self.assertIsNone(repository.get(inserted_before_failure.incident_id))

    def test_evidence_union_is_bounded_and_always_preserves_anchor(self) -> None:
        first_events = tuple(evidence(EvidenceKind.JOURNAL_EVENT, f"cursor:{index:03}",
                                      NOW + timedelta(microseconds=index + 1))
                             for index in range(MAX_EVIDENCE_PER_INCIDENT - 1))
        second_events = tuple(evidence(EvidenceKind.JOURNAL_EVENT, f"cursor:{index:03}",
                                       NOW + timedelta(microseconds=index + 1))
                              for index in range(MAX_EVIDENCE_PER_INCIDENT - 1, 254))
        first = candidate(last_observed_at=NOW + timedelta(seconds=1), extra_evidence=first_events)
        second = candidate(last_observed_at=NOW + timedelta(seconds=1), extra_evidence=second_events)
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((first,), resolved_subjects=(), observed_at=first.last_observed_at,
                                 resolution_authoritative=False)
            repository.reconcile((second,), resolved_subjects=(), observed_at=second.last_observed_at,
                                 resolution_authoritative=False)
            stored = repository.get(first.incident_id)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(len(stored.evidence), MAX_EVIDENCE_PER_INCIDENT)
            self.assertIn("service-change:1", {item.evidence_id for item in stored.evidence})
            self.assertIn("cursor:253", {item.evidence_id for item in stored.evidence})
            self.assertIn(EVIDENCE_LIMIT_REACHED, stored.quality_limitations)

    def test_recent_queries_are_bounded_filtered_and_deterministic(self) -> None:
        alpha = candidate("anchor:a")
        beta = candidate("anchor:b")
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((beta, alpha), resolved_subjects=(), observed_at=beta.last_observed_at,
                                 resolution_authoritative=False)
            expected = tuple(sorted((alpha.incident_id, beta.incident_id)))
            self.assertEqual(tuple(item.incident_id for item in repository.list_recent()), expected)
            self.assertEqual(len(repository.list_recent(state=IncidentState.ACTIVE, subject=SUBJECT)), 2)
            with self.assertRaises(ValueError):
                repository.list_recent(limit=1001)

    def test_resolved_retention_cascades_evidence_and_preserves_active_incidents(self) -> None:
        resolved = candidate("anchor:resolved")
        active = candidate("anchor:active", subject_id="worker.service")
        cutoff = NOW + timedelta(seconds=10)
        with self.connection() as connection:
            initialize_schema(connection)
            repository = IncidentRepository(connection)
            repository.reconcile((resolved, active), resolved_subjects=(),
                                 observed_at=resolved.last_observed_at,
                                 resolution_authoritative=False)
            repository.reconcile((), resolved_subjects=(SUBJECT,), observed_at=NOW + timedelta(seconds=5),
                                 resolution_authoritative=True)
            self.assertEqual(delete_resolved_incidents_before(connection, cutoff), 1)
            self.assertIsNone(repository.get(resolved.incident_id))
            self.assertIsNotNone(repository.get(active.incident_id))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM incident_evidence").fetchone()[0], 2)
            with self.assertRaises(ValueError):
                delete_resolved_incidents_before(connection, datetime(2026, 1, 3))


class IncidentModelTests(unittest.TestCase):
    def test_incident_requires_correlated_kinds_and_one_compatible_anchor(self) -> None:
        anchor = evidence(EvidenceKind.SERVICE_CHANGE, "anchor", NOW)
        with self.assertRaises(ValueError):
            IncidentCandidate(identity("anchor"), "rule", SubjectType.SERVICE, SUBJECT[1],
                              IncidentState.ACTIVE, NOW, NOW, None, (anchor,))
        with self.assertRaises(ValueError):
            EvidenceReference(EvidenceKind.JOURNAL_EVENT, "cursor", NOW, SubjectType.SERVICE,
                              SUBJECT[1], EvidenceRelation.SERVICE_CHANGE_ANCHOR)

    def test_incident_rejects_non_utc_or_out_of_range_evidence(self) -> None:
        with self.assertRaises(ValueError):
            evidence(EvidenceKind.JOURNAL_EVENT, "cursor", datetime(2026, 1, 2))
        late = evidence(EvidenceKind.JOURNAL_EVENT, "late", NOW + timedelta(seconds=2))
        with self.assertRaises(ValueError):
            candidate(last_observed_at=NOW + timedelta(seconds=1), extra_evidence=(late,))
