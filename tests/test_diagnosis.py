from dataclasses import replace
from datetime import UTC, datetime, timedelta
import unittest

from sentinel.analysis import (
    ComparisonState, DiagnosisState, DiagnosticRule, ProcessLifecycle,
    ServiceLifecycle, ServiceProcessAssociation, TimedProcessChange, TimedServiceChange,
    correlate_service_incidents, diagnose_incident,
)
from sentinel.analysis.processes import ProcessChange
from sentinel.analysis.services import ServiceChange
from sentinel.analysis.temporal import CounterComparison
from sentinel.models import (
    CollectionStatus, EvidenceFact, EventObservation, ProcessObservation, ServiceObservation,
)

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def service_failure(at: datetime = NOW, limitations: tuple[str, ...] = ()) -> TimedServiceChange:
    previous = ServiceObservation("api.service", "loaded", "active", "running")
    current = ServiceObservation("api.service", "loaded", "failed", "failed")
    return TimedServiceChange(
        ServiceChange(ServiceLifecycle.STATE_CHANGED, "api.service", previous, current),
        at,
        limitations,
    )


def journal(cursor: str = "cursor", at: datetime = NOW) -> EventObservation:
    return EventObservation(cursor, at, "systemd", 3, "api.service", 22, "api",
                            "raw private body", "boot")


def process(lifetime: str = "22:100", lifecycle: ProcessLifecycle = ProcessLifecycle.EXITED,
            at: datetime = NOW) -> TimedProcessChange:
    pid, start = (int(value) for value in lifetime.split(":"))
    observation = ProcessObservation(pid, 1, "api", "S", 1, 1, 1, 1, start, "private", "/bin/api")
    previous = observation if lifecycle is ProcessLifecycle.EXITED else None
    current = None if lifecycle is ProcessLifecycle.EXITED else observation
    return TimedProcessChange(ProcessChange(
        lifecycle, lifetime, pid, previous, current,
        CounterComparison(ComparisonState.REMOVED_RESOURCE), None, None,
    ), at)


def candidate(*, include_journal: bool = True, process_lifecycle: ProcessLifecycle | None = None,
              status: CollectionStatus = CollectionStatus.SUCCESS, at: datetime = NOW):
    processes = () if process_lifecycle is None else (process(lifecycle=process_lifecycle, at=at),)
    associations = () if not processes else (ServiceProcessAssociation("api.service", "22:100"),)
    events = (journal(at=at),) if include_journal else ()
    return correlate_service_incidents(
        (service_failure(at),), processes, events, associations=associations,
        journal_status=status,
    )[0]


class DiagnosisTests(unittest.TestCase):
    def test_combined_rule_is_strongest_and_explanation_does_not_claim_cause(self) -> None:
        result = diagnose_incident(candidate(process_lifecycle=ProcessLifecycle.EXITED))
        self.assertEqual(result.state, DiagnosisState.SUPPORTED)
        self.assertEqual(result.rule, DiagnosticRule.SERVICE_FAILURE_JOURNAL_PROCESS_EXIT)
        self.assertEqual(result.supported_facts,
                         ("service_failed", "exact_unit_journal_event",
                          "associated_process_lifetime_exited"))
        self.assertIn("exact root cause", result.explanation)
        self.assertIn("causal direction", result.explanation)
        self.assertNotIn("raw private body", result.explanation)
        self.assertNotIn("/bin/api", result.explanation)

    def test_journal_and_process_rules_are_independently_supported(self) -> None:
        journal_result = diagnose_incident(candidate())
        process_result = diagnose_incident(candidate(
            include_journal=False, process_lifecycle=ProcessLifecycle.EXITED,
        ))
        self.assertEqual(journal_result.rule, DiagnosticRule.SERVICE_FAILURE_JOURNAL)
        self.assertEqual(process_result.rule, DiagnosticRule.SERVICE_FAILURE_PROCESS_EXIT)

    def test_nontermination_process_evidence_is_insufficient(self) -> None:
        item = candidate(include_journal=False, process_lifecycle=ProcessLifecycle.NEW)
        result = diagnose_incident(item)
        self.assertEqual(result.state, DiagnosisState.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.rule, DiagnosticRule.SERVICE_FAILURE_INSUFFICIENT)
        self.assertEqual({entry.fact for entry in result.provenance}, {EvidenceFact.SERVICE_FAILED})

    def test_legacy_unknown_fact_is_unknown_instead_of_parsing_summary(self) -> None:
        item = candidate()
        evidence = tuple(replace(entry, fact=EvidenceFact.UNKNOWN) for entry in item.evidence)
        legacy = replace(item, evidence=evidence)
        result = diagnose_incident(legacy)
        self.assertEqual(result.state, DiagnosisState.UNKNOWN)
        self.assertIn("legacy_evidence_fact_unknown", result.limitations)

    def test_partial_or_failed_context_remains_visible(self) -> None:
        partial = diagnose_incident(candidate(status=CollectionStatus.PARTIAL))
        denied = diagnose_incident(candidate(status=CollectionStatus.PERMISSION_DENIED))
        failed = diagnose_incident(candidate(
            include_journal=False,
            process_lifecycle=ProcessLifecycle.EXITED,
            status=CollectionStatus.TRANSIENT_FAILURE,
        ))
        self.assertIn("journal_collection_partial", partial.limitations)
        self.assertIn("journal_collection_permission_denied", denied.limitations)
        self.assertIn("journal_collection_transient_failure", failed.limitations)
        self.assertIn("Evidence context is incomplete", partial.explanation)
        self.assertIn("Evidence context is incomplete", failed.explanation)
        self.assertEqual(partial.state, DiagnosisState.SUPPORTED)

    def test_provenance_is_deterministic_and_repeated_diagnosis_is_identical(self) -> None:
        item = candidate(process_lifecycle=ProcessLifecycle.EXITED)
        first = diagnose_incident(item)
        second = diagnose_incident(item)
        self.assertEqual(first, second)
        self.assertEqual(tuple((entry.kind.value, entry.evidence_id) for entry in first.provenance),
                         tuple(sorted((entry.kind.value, entry.evidence_id)
                                      for entry in first.provenance)))

    def test_historical_context_is_bounded_and_never_called_a_recurring_cause(self) -> None:
        current_time = NOW + timedelta(minutes=30)
        current = candidate(at=current_time)
        history = tuple(candidate(at=NOW + timedelta(minutes=index)) for index in range(25))
        result = diagnose_incident(current, historical_incidents=reversed(history))
        self.assertEqual(len(result.historical_incident_ids), 20)
        self.assertIn("does not establish a recurring cause", result.explanation)

    def test_invalid_input_and_conflicting_history_remain_visible(self) -> None:
        with self.assertRaises(TypeError):
            diagnose_incident("incident")  # type: ignore[arg-type]
        item = candidate()
        conflict = replace(item, quality_limitations=("different",))
        with self.assertRaises(ValueError):
            diagnose_incident(candidate(at=NOW + timedelta(minutes=1)),
                              historical_incidents=(item, conflict))

    def test_diagnosis_contract_rejects_malformed_provenance_and_history(self) -> None:
        result = diagnose_incident(candidate())
        with self.assertRaises(TypeError):
            replace(result, provenance=("not-evidence",))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            replace(result, historical_incident_ids=("not-an-incident-id",))
