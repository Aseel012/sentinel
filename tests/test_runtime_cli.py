import contextlib
from datetime import UTC, datetime
import io
import json
import signal
import unittest
from unittest.mock import patch

from sentinel.application.runtime_models import RuntimeCycle
from sentinel.application.runtime_ownership import (
    RUNTIME_OWNERSHIP_EXIT_CODE,
    RuntimeAlreadyOwned,
)
from sentinel.application.sampler import SamplingRun
from sentinel.main import main
from sentinel.models import CollectionStatus


NOW = datetime(2026, 8, 1, tzinfo=UTC)

def cycle() -> RuntimeCycle:
    return RuntimeCycle(
        1, NOW, 1.0, 1.25, 0.25, 7,
        (("processes", CollectionStatus.PARTIAL),
         ("services", CollectionStatus.SUCCESS)),
        (("processes", 2), ("services", 1)),
        CollectionStatus.SUCCESS, 1, 1,
        ("a" * 64,), ("a" * 64,), (),
        ("collector_processes_partial",),
    )

class FakeRuntime:
    instance = None

    def __init__(self, _path, config, *, event_recorder, cycle_sink) -> None:
        self.config = config
        self.event_recorder = event_recorder
        self.cycle_sink = cycle_sink
        self.max_cycles = None
        self.stop_requested = False
        FakeRuntime.instance = self

    def request_stop(self) -> None:
        self.stop_requested = True

    def run(self, *, max_cycles=None):
        self.max_cycles = max_cycles
        self.cycle_sink(cycle())
        return SamplingRun(1, self.stop_requested)


class RuntimeCLITests(unittest.TestCase):
    def test_finite_json_run_has_stable_private_cycle_contract(self) -> None:
        output = io.StringIO()
        with patch("sentinel.main.ContinuousObservationRuntime", FakeRuntime), \
                contextlib.redirect_stdout(output):
            code = main(["run", "--cycles", "1", "--interval", "0.5",
                         "--event-limit", "10", "--json"])
        response = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(response["schema_version"], "1")
        self.assertEqual(response["cycle"]["snapshot_id"], 7)
        self.assertEqual(response["cycle"]["journal_status"], "success")
        self.assertEqual(FakeRuntime.instance.max_cycles, 1)
        self.assertEqual(FakeRuntime.instance.config.interval_seconds, 0.5)
        self.assertNotIn("message", output.getvalue())
        self.assertNotIn("command", output.getvalue())
        self.assertNotIn("executable", output.getvalue())

    def test_text_run_and_signal_handlers_are_restored(self) -> None:
        before = {item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)}
        output = io.StringIO()
        with patch("sentinel.main.ContinuousObservationRuntime", FakeRuntime), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(["run", "--cycles", "1"]), 0)
        self.assertIn("Cycle 1 completed", output.getvalue())
        self.assertEqual({item: signal.getsignal(item) for item in before}, before)

    def test_ownership_failure_is_bounded_and_restores_signal_handlers(self) -> None:
        before = {item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)}
        error = io.StringIO()
        with patch.object(FakeRuntime, "run", side_effect=RuntimeAlreadyOwned("private")), \
                patch("sentinel.main.ContinuousObservationRuntime", FakeRuntime), \
                contextlib.redirect_stderr(error):
            code = main(["run", "--cycles", "1"])
        self.assertEqual(code, RUNTIME_OWNERSHIP_EXIT_CODE)
        self.assertEqual(error.getvalue(), "sentinel run: another runtime owns this database\n")
        self.assertNotIn("Traceback", error.getvalue())
        self.assertNotIn("private", error.getvalue())
        self.assertEqual({item: signal.getsignal(item) for item in before}, before)

    def test_runtime_bounds_are_rejected_before_runtime_construction(self) -> None:
        invalid = (
            ["run", "--cycles", "0"],
            ["run", "--cycles", "100001"],
            ["run", "--interval", "0.01"],
            ["run", "--interval", "nan"],
            ["run", "--event-limit", "1001"],
            ["run", "--snapshot-limit", "0"],
        )
        for arguments in invalid:
            FakeRuntime.instance = None
            with self.subTest(arguments=arguments), \
                    patch("sentinel.main.ContinuousObservationRuntime", FakeRuntime), \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                main(arguments)
            self.assertEqual(raised.exception.code, 2)
            self.assertIsNone(FakeRuntime.instance)
