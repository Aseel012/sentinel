from datetime import UTC, datetime
import unittest
import subprocess
from unittest.mock import patch

from sentinel.analysis import ServiceLifecycle, compare_service_collections
from sentinel.application.snapshot_service import SnapshotCollection
from sentinel.models import CollectionResult, CollectionStatus, ServiceObservation, SystemSnapshot
from sentinel.collectors.services import collect_services, parse_services
from sentinel.collectors.subprocesses import OutputLimitExceeded


def service(name: str, state: str = "active") -> ServiceObservation:
    return ServiceObservation(name, "loaded", state, "running" if state == "active" else "dead")


def collection(services: tuple[ServiceObservation, ...], status: CollectionStatus = CollectionStatus.SUCCESS) -> SnapshotCollection:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    result = CollectionResult(services if status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL) else None,
                              status, timestamp, 0.1)
    snapshot = SystemSnapshot(timestamp, None, None, None, (), (), (),
                              services if result.value is not None else (), (("services", status.value),))
    return SnapshotCollection(snapshot, (("services", result),))


class ServiceIntelligenceTests(unittest.TestCase):
    def test_first_sample_has_insufficient_history(self) -> None:
        result = compare_service_collections(None, collection((service("api.service"),)))
        self.assertEqual(result.changes[0].lifecycle, ServiceLifecycle.INSUFFICIENT_HISTORY)

    def test_continuing_state_change_new_and_removed_are_distinct(self) -> None:
        previous = collection((service("api.service"), service("old.service")))
        current = collection((service("api.service", "failed"), service("new.service")))
        changes = {change.name: change.lifecycle for change in compare_service_collections(previous, current).changes}
        self.assertEqual(changes["api.service"], ServiceLifecycle.STATE_CHANGED)
        self.assertEqual(changes["new.service"], ServiceLifecycle.NEW)
        self.assertEqual(changes["old.service"], ServiceLifecycle.REMOVED)

    def test_partial_sample_never_claims_removed_services(self) -> None:
        previous = collection((service("api.service"), service("worker.service")))
        current = collection((service("api.service"),), CollectionStatus.PARTIAL)
        result = compare_service_collections(previous, current)
        self.assertEqual([change.lifecycle for change in result.changes], [ServiceLifecycle.CONTINUING])
        self.assertFalse(result.current_collection_complete)

    def test_unavailable_services_make_no_lifecycle_claim(self) -> None:
        result = compare_service_collections(collection((service("api.service"),)),
                                             collection((), CollectionStatus.UNSUPPORTED))
        self.assertEqual(result.changes, ())

    def test_duplicate_service_name_is_invalid_input(self) -> None:
        duplicate = collection((service("api.service"), service("api.service")))
        for previous, current in ((duplicate, collection((service("api.service"),))),
                                  (None, duplicate), (collection(()), duplicate)):
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                compare_service_collections(previous, current)

    def test_invalid_state_is_not_a_change_or_valid_history(self) -> None:
        invalid = collection((ServiceObservation("api.service", "", "active", "running"),))
        for previous, current in ((None, invalid), (invalid, collection((service("api.service"),))),
                                  (collection((service("api.service"),)), invalid)):
            with self.subTest(previous=previous):
                result = compare_service_collections(previous, current)
                self.assertEqual(result.changes[0].lifecycle, ServiceLifecycle.INVALID)
        result = compare_service_collections(invalid, collection((service("new.service"),)))
        self.assertEqual([item.lifecycle for item in result.changes], [ServiceLifecycle.INSUFFICIENT_HISTORY])

    def test_invalid_identity_is_explicit(self) -> None:
        for name in ("", ".service", "api.socket", "bad name.service"):
            with self.subTest(name=name):
                result = compare_service_collections(None, collection((service(name),)))
                self.assertEqual(result.changes[0].lifecycle, ServiceLifecycle.INVALID)
                self.assertFalse(result.current_collection_complete)

    def test_partial_collections_do_not_claim_new_or_removed(self) -> None:
        for previous_status, current_status in ((CollectionStatus.PARTIAL, CollectionStatus.SUCCESS),
                                                (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL)):
            result = compare_service_collections(collection((service("old.service"),), previous_status),
                                                 collection((service("new.service"),), current_status))
            self.assertEqual([item.lifecycle for item in result.changes], [ServiceLifecycle.INSUFFICIENT_HISTORY])

    def test_failure_is_not_empty_and_recovery_needs_history(self) -> None:
        for status in (CollectionStatus.UNSUPPORTED, CollectionStatus.TRANSIENT_FAILURE,
                       CollectionStatus.PERMISSION_DENIED, CollectionStatus.INVALID_DATA):
            with self.subTest(status=status):
                failed = collection((), status)
                healthy = collection((service("api.service"),))
                self.assertEqual(compare_service_collections(healthy, failed).changes, ())
                self.assertEqual(compare_service_collections(failed, healthy).changes[0].lifecycle,
                                 ServiceLifecycle.INSUFFICIENT_HISTORY)


class ServiceCollectorTests(unittest.TestCase):
    def test_parser_preserves_canonical_names_and_states(self) -> None:
        self.assertEqual(parse_services("worker@one.service loaded active running Worker\n"),
                         (service("worker@one.service"),))
        self.assertEqual(parse_services(""), ())

    def test_parser_rejects_malformed_or_duplicate_units(self) -> None:
        for text in ("broken", "api.socket loaded active running", ".service loaded active running",
                     "api.service loaded active running\napi.service loaded failed dead"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_services(text)

    def test_missing_systemctl_is_unsupported(self) -> None:
        with patch("sentinel.collectors.services.which", return_value=None):
            self.assertEqual(collect_services().status, CollectionStatus.UNSUPPORTED)

    def test_expected_execution_errors_are_structured(self) -> None:
        for error, status in ((PermissionError(), CollectionStatus.PERMISSION_DENIED),
                              (FileNotFoundError(), CollectionStatus.TRANSIENT_FAILURE),
                              (UnicodeError(), CollectionStatus.INVALID_DATA),
                              (subprocess.TimeoutExpired("systemctl", 5), CollectionStatus.TRANSIENT_FAILURE),
                              (OutputLimitExceeded(), CollectionStatus.INVALID_DATA)):
            with self.subTest(error=error), patch("sentinel.collectors.services.which", return_value="/bin/systemctl"), \
                    patch("sentinel.collectors.services.run_bounded", side_effect=error):
                self.assertEqual(collect_services().status, status)

    def test_subprocess_success_empty_denied_and_unavailable_are_distinct(self) -> None:
        cases = ((0, b"", b"", CollectionStatus.SUCCESS),
                 (0, b"api.service loaded active running Description", b"", CollectionStatus.SUCCESS),
                 (1, b"", b"Access denied", CollectionStatus.PERMISSION_DENIED),
                 (1, b"", b"Failed to connect to bus", CollectionStatus.UNSUPPORTED),
                 (0, b"\xff", b"", CollectionStatus.INVALID_DATA))
        for code, stdout, stderr, status in cases:
            with self.subTest(status=status), patch("sentinel.collectors.services.which", return_value="/bin/systemctl"), \
                    patch("sentinel.collectors.services.run_bounded",
                          return_value=subprocess.CompletedProcess([], code, stdout, stderr)) as run:
                result = collect_services()
                self.assertEqual(result.status, status)
                self.assertEqual(run.call_args.kwargs, {"timeout": 5, "output_limit": 1_000_000})
                if status is not CollectionStatus.SUCCESS:
                    self.assertIsNone(result.value)
