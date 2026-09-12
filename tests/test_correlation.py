from datetime import UTC, datetime, timedelta
import unittest

from sentinel.analysis import (
    ComparisonState,
    ProcessLifecycle,
    ServiceLifecycle,
    ServiceProcessAssociation,
    TimedProcessChange,
    TimedServiceChange,
    correlate_service_incidents,
    resolution_subjects,
)
from sentinel.analysis.processes import ProcessChange
from sentinel.analysis.services import ServiceChange
from sentinel.analysis.temporal import CounterComparison
from sentinel.models import (
    CollectionStatus,
    EvidenceKind,
    EventObservation,
    ProcessObservation,
    ServiceObservation,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def service(name: str, active: str, sub: str | None = None) -> ServiceObservation:
    return ServiceObservation(name, "loaded", active, sub or active)


def service_change(
    *,
    name: str = "api.service",
    at: datetime = NOW,
    lifecycle: ServiceLifecycle = ServiceLifecycle.STATE_CHANGED,
    previous_active: str | None = "active",
    current_active: str | None = "failed",
    limitations: tuple[str, ...] = (),
) -> TimedServiceChange:
    previous = None if previous_active is None else service(name, previous_active)
    current = None if current_active is None else service(name, current_active)
    return TimedServiceChange(ServiceChange(lifecycle, name, previous, current), at, limitations)


def process_change(
    *,
    lifetime: str = "12:100",
    at: datetime = NOW,
    lifecycle: ProcessLifecycle = ProcessLifecycle.EXITED,
) -> TimedProcessChange:
    pid, started = (int(part) for part in lifetime.split(":"))
    observation = ProcessObservation(pid, 1, "api", "S", 10, 20, 1, 3, started, None, None)
    previous = observation if lifecycle is ProcessLifecycle.EXITED else None
    current = None if lifecycle is ProcessLifecycle.EXITED else observation
    change = ProcessChange(
        lifecycle, lifetime, pid, previous, current,
        CounterComparison(ComparisonState.REMOVED_RESOURCE), None, None,
    )
    return TimedProcessChange(change, at)


def event(
    cursor: str,
    *,
    at: datetime = NOW,
    unit: str | None = "api.service",
    message: str = "sensitive raw message",
) -> EventObservation:
    return EventObservation(cursor, at, "systemd", 3, unit, 12, "api", message, "boot")


class CorrelationTests(unittest.TestCase):
    def test_service_and_exact_unit_event_form_structured_evidence(self) -> None:
        candidate = correlate_service_incidents(
            (service_change(),), (), (event("cursor-1"),),
        )[0]
        self.assertEqual(candidate.subject_id, "api.service")
        self.assertEqual({item.kind for item in candidate.evidence}, {
            EvidenceKind.SERVICE_CHANGE, EvidenceKind.JOURNAL_EVENT,
        })
        self.assertNotIn("sensitive raw message", " ".join(
            item.summary or "" for item in candidate.evidence
        ))

    def test_window_is_inclusive_and_precise(self) -> None:
        window = timedelta(seconds=5)
        for offset, expected in ((4.999999, 1), (5, 1), (5.000001, 0), (-5, 1)):
            with self.subTest(offset=offset):
                candidates = correlate_service_incidents(
                    (service_change(),), (),
                    (event("cursor", at=NOW + timedelta(seconds=offset)),),
                    correlation_window=window,
                )
                self.assertEqual(len(candidates), expected)

    def test_time_or_similar_names_and_pids_do_not_create_relationships(self) -> None:
        process = process_change()
        candidates = correlate_service_incidents(
            (service_change(),), (process,),
            (event("other", unit="api-worker.service"),),
        )
        self.assertEqual(candidates, ())
        associated = correlate_service_incidents(
            (service_change(),), (process,), (),
            associations=(ServiceProcessAssociation("api.service", "12:100"),),
        )
        self.assertEqual(len(associated), 1)

    def test_process_lifetime_identity_controls_association(self) -> None:
        change = process_change(lifetime="12:101", lifecycle=ProcessLifecycle.IDENTITY_CHANGED)
        old_identity = correlate_service_incidents(
            (service_change(),), (change,), (),
            associations=(ServiceProcessAssociation("api.service", "12:100"),),
        )
        new_identity = correlate_service_incidents(
            (service_change(),), (change,), (),
            associations=(ServiceProcessAssociation("api.service", "12:101"),),
        )
        self.assertEqual(old_identity, ())
        self.assertEqual(len(new_identity), 1)
        for identity in ("12", "pid:start", "0:10", "12:-1", "12:10 extra"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                ServiceProcessAssociation("api.service", identity)

    def test_duplicate_inputs_are_idempotent_and_output_is_deterministic(self) -> None:
        anchor = service_change()
        one = event("one", at=NOW - timedelta(seconds=1))
        two = event("two", at=NOW + timedelta(seconds=1))
        first = correlate_service_incidents((anchor, anchor), (), (two, one, one))
        second = correlate_service_incidents((anchor,), (), (one, two))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)

    def test_distinct_service_occurrences_have_distinct_stable_identities(self) -> None:
        first_anchor = service_change(at=NOW)
        later = NOW + timedelta(minutes=1)
        second_anchor = service_change(at=later)
        first = correlate_service_incidents((first_anchor,), (), (event("one"),))[0]
        repeated = correlate_service_incidents((first_anchor,), (), (event("one"),))[0]
        second = correlate_service_incidents(
            (second_anchor,), (), (event("two", at=later),),
        )[0]
        self.assertEqual(first.incident_id, repeated.incident_id)
        self.assertNotEqual(first.incident_id, second.incident_id)

    def test_service_identities_form_separate_deterministically_ordered_incidents(self) -> None:
        worker = service_change(name="worker.service")
        api = service_change(name="api.service")
        candidates = correlate_service_incidents(
            (worker, api), (),
            (event("worker", unit="worker.service"), event("api", unit="api.service")),
        )
        self.assertEqual([item.subject_id for item in candidates],
                         ["api.service", "worker.service"])

    def test_anchor_start_is_stable_when_earlier_evidence_arrives_later(self) -> None:
        anchor = service_change()
        first = correlate_service_incidents((anchor,), (), (event("same-time"),))[0]
        expanded = correlate_service_incidents(
            (anchor,), (), (event("earlier", at=NOW - timedelta(seconds=2)), event("same-time")),
        )[0]
        self.assertEqual(first.incident_id, expanded.incident_id)
        self.assertEqual(first.started_at, NOW)
        self.assertEqual(expanded.started_at, NOW)

    def test_only_failure_transitions_and_removals_anchor_candidates(self) -> None:
        changes = (
            service_change(lifecycle=ServiceLifecycle.CONTINUING),
            service_change(lifecycle=ServiceLifecycle.NEW, previous_active=None),
            service_change(lifecycle=ServiceLifecycle.INSUFFICIENT_HISTORY, previous_active=None),
            service_change(lifecycle=ServiceLifecycle.INVALID),
            service_change(current_active="active", previous_active="failed"),
        )
        for change in changes:
            with self.subTest(lifecycle=change.change.lifecycle):
                self.assertEqual(
                    correlate_service_incidents((change,), (), (event("event"),)), (),
                )
        removed = service_change(
            lifecycle=ServiceLifecycle.REMOVED, current_active=None,
        )
        self.assertEqual(len(correlate_service_incidents(
            (removed,), (), (event("removed"),),
        )), 1)
        malformed = TimedServiceChange(ServiceChange(
            ServiceLifecycle.STATE_CHANGED, "api.service",
            ServiceObservation("api.service", "loaded", "x" * 129, "running"),
            service("api.service", "failed"),
        ), NOW)
        self.assertEqual(correlate_service_incidents((malformed,), (), (event("bad"),)), ())

    def test_resolution_requires_an_explicit_transition_to_active(self) -> None:
        resolved = service_change(previous_active="failed", current_active="active")
        new_active = service_change(
            lifecycle=ServiceLifecycle.NEW, previous_active=None, current_active="active",
        )
        continuing = service_change(
            lifecycle=ServiceLifecycle.CONTINUING, current_active="active",
        )
        self.assertEqual(resolution_subjects((new_active, resolved, continuing, resolved)),
                         ("api.service",))

    def test_failed_journal_quality_is_preserved_only_on_formed_candidate(self) -> None:
        formed = correlate_service_incidents(
            (service_change(limitations=("service_partial",)),), (process_change(),), (),
            associations=(ServiceProcessAssociation("api.service", "12:100"),),
            journal_status=CollectionStatus.PERMISSION_DENIED,
        )[0]
        self.assertEqual(formed.quality_limitations,
                         ("journal_collection_permission_denied", "service_partial"))
        self.assertEqual(correlate_service_incidents(
            (service_change(),), (), (), journal_status=CollectionStatus.PERMISSION_DENIED,
        ), ())
        partial = correlate_service_incidents(
            (service_change(),), (), (event("partial"),),
            journal_status=CollectionStatus.PARTIAL,
        )[0]
        self.assertEqual(partial.quality_limitations, ("journal_collection_partial",))

    def test_invalid_or_conflicting_event_evidence_is_handled_explicitly(self) -> None:
        invalid_service = service_change(name="", lifecycle=ServiceLifecycle.INVALID)
        self.assertEqual(correlate_service_incidents(
            (invalid_service,), (), (event("unrelated"),),
        ), ())
        naive = event("naive", at=datetime(2026, 1, 1), unit="api.service")
        with self.assertRaises(ValueError):
            correlate_service_incidents((service_change(),), (), (naive,))
        empty_cursor = event("")
        with self.assertRaises(ValueError):
            correlate_service_incidents((service_change(),), (), (empty_cursor,))
        conflict = EventObservation(
            "same", NOW, "other", 3, "api.service", 12, "api", "different", "boot",
        )
        with self.assertRaises(ValueError):
            correlate_service_incidents(
                (service_change(),), (), (event("same"), conflict),
            )

    def test_evidence_is_bounded_by_proximity_then_ordered_deterministically(self) -> None:
        events = tuple(event(f"cursor-{offset}", at=NOW + timedelta(seconds=offset))
                       for offset in (5, -5, 1, -1, 0))
        candidate = correlate_service_incidents(
            (service_change(),), (), events, max_evidence=3,
        )[0]
        self.assertEqual(len(candidate.evidence), 3)
        journal_ids = [item.evidence_id for item in candidate.evidence
                       if item.kind is EvidenceKind.JOURNAL_EVENT]
        self.assertEqual(journal_ids, ["journal:cursor--1", "journal:cursor-0"])

    def test_options_and_input_timestamps_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            TimedServiceChange(service_change().change, datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            TimedServiceChange(service_change().change, NOW, ("duplicate", "duplicate"))
        for window in (timedelta(seconds=-1),):
            with self.subTest(window=window), self.assertRaises(ValueError):
                correlate_service_incidents((), (), (), correlation_window=window)
        for limit in (True, 1, 129):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                correlate_service_incidents((), (), (), max_evidence=limit)
        with self.assertRaises(ValueError):
            correlate_service_incidents(
                (service_change() for _ in range(3)), (), (), max_inputs=2,
            )
        with self.assertRaises(ValueError):
            resolution_subjects((service_change() for _ in range(3)), max_inputs=2)


if __name__ == "__main__":
    unittest.main()
