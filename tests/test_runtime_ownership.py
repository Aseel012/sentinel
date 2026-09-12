import os
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sentinel.application.runtime_ownership import RuntimeAlreadyOwned, RuntimeLease
from sentinel.main import main
from sentinel.storage.database import database_connection
from sentinel.storage.migrations import initialize_schema


class RuntimeOwnershipTests(unittest.TestCase):
    def test_second_owner_is_rejected_and_normal_release_allows_reacquire(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            first = RuntimeLease(path)
            second = RuntimeLease(path)
            with first:
                self.assertTrue(first.acquired)
                with self.assertRaises(RuntimeAlreadyOwned):
                    second.acquire()
            self.assertFalse(first.acquired)
            with second:
                self.assertTrue(second.acquired)
                second.release()
                second.release()
            self.assertFalse(second.acquired)
            self.assertEqual(second.path.stat().st_mode & 0o777, 0o600)

    def test_abnormal_process_exit_releases_kernel_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            code = (
                "import os,sys; "
                "from sentinel.application.runtime_ownership import RuntimeLease; "
                "lease=RuntimeLease(sys.argv[1]); lease.acquire(); os._exit(0)"
            )
            completed = subprocess.run(
                [sys.executable, "-c", code, str(path)],
                cwd=Path(__file__).parents[1],
                env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "."},
                timeout=5,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            with RuntimeLease(path) as recovered:
                self.assertTrue(recovered.acquired)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "requires O_NOFOLLOW")
    def test_lock_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            target = Path(directory) / "target"
            target.write_text("do not alter", encoding="utf-8")
            lease = RuntimeLease(path)
            lease.path.symlink_to(target)
            with self.assertRaises(OSError):
                lease.acquire()
            self.assertEqual(target.read_text(encoding="utf-8"), "do not alter")

    def test_read_only_cli_access_remains_available_to_runtime_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            with database_connection(path) as connection:
                initialize_schema(connection)
            incidents_output = io.StringIO()
            missing_output = io.StringIO()
            with RuntimeLease(path), \
                    contextlib.redirect_stdout(incidents_output):
                self.assertEqual(
                    main(["incidents", "--database", str(path), "--json"]),
                    0,
                )
            with RuntimeLease(path), \
                    contextlib.redirect_stdout(missing_output):
                self.assertEqual(
                    main(["diagnose", "0" * 64, "--database", str(path), "--json"]),
                    1,
                )
            self.assertEqual(json.loads(incidents_output.getvalue())["incidents"], [])
            self.assertEqual(
                json.loads(missing_output.getvalue())["error"]["code"],
                "incident_not_found",
            )
