# Phase 11 validation

## Audit findings and decisions

The Phase 10 runtime had sound explicit SQLite transactions, bounded collection/query inputs, monotonic scheduling, conservative comparisons, and deterministic incident identity. The primary reliability defect was the absence of process-wide ownership: separate runtimes could collect and write the same database concurrently. A second privacy/reliability defect was that the one-cycle temporal baseline retained a complete prior snapshot, including command/executable fields and diagnostics that temporal comparison never uses.

Phase 11 adds one narrow Linux-native ownership component. `RuntimeLease` derives an adjacent path from the canonically resolved database path, opens it without following symlinks, enforces a regular owner-only file, and requests non-blocking exclusive `flock` ownership. The close-on-exec descriptor stays open for the complete `run()`. Contention raises the dedicated `RuntimeAlreadyOwned` error; the CLI converts only that expected condition to bounded stderr and exit code 3. Filesystem, SQLite, corruption, and invariant exceptions retain their distinct types and remain visible.

The lock file is not a lease record. It contains no PID or timestamp, is never interpreted as stale, and is never automatically removed. Normal release closes the descriptor. Linux also releases it when a process exits without cleanup, so an abandoned file cannot brick the runtime and no PID-reuse heuristic is needed. This is local single-host ownership, not a distributed lock.

The temporal baseline now retains only sanitized process facts, service observations, their collection quality/timestamps, and the snapshot timestamp. Command lines, executable paths, system/memory/CPU/disk/network observations, warnings, errors, and collector durations are discarded from long-lived runtime state. The full bounded snapshot remains durable under the established local-storage privacy model. The baseline advances only after every application stage produces a valid completed cycle.

## Restart, transactions, and retention

Restart intentionally loses the monotonic point and temporal baseline. The first new observation is insufficient history even on the same boot; persisted wall time is not converted into elapsed time. This prevents fabricated exits, removals, transitions, rates, and resolution. Durable snapshots, events, journal checkpoint, incidents, and evidence remain. The next journal collection resumes from the checkpoint, and bounded persisted event windows can support a later real service transition.

Snapshot, journal, and incident writes remain separate transactions. A later failure does not roll back an earlier committed stage and is never reported as a completed cycle. Each repository transaction rolls back its own failed work. Foreign keys remain enabled, WAL allows readers beside the runtime writer, and the SQLite busy timeout remains five seconds. There are no hidden retries or destructive corruption recovery.

Snapshot retention cascades only source-observation children. Event retention preserves the ingestion checkpoint. Incident/evidence retention remains independent, and active incidents are not automatically deleted. SQLite freed pages are not a byte quota or secure erase. Phase 11 adds no new growing table, history cache, log, temporary file, or subprocess.

## Failure matrix

| Failure | Durable state | Runtime state | Expected result |
|---|---|---|---|
| snapshot collector partial | Snapshot and quality commit | Sanitized baseline advances after full cycle | Continue; incomplete inventory cannot prove negative facts |
| snapshot persistence failure | Failed snapshot transaction rolls back | Baseline/cycle count unchanged | Storage error propagates |
| journal partial | Accepted bounded events/checkpoint and quality commit | Baseline advances after full cycle | Continue with limitation |
| journal failure result | Failure quality commits; checkpoint unchanged | Baseline advances after full cycle | Continue without journal evidence |
| event query failure | Earlier snapshot/journal commits remain | Baseline/cycle count unchanged | Error propagates; no completed cycle output |
| correlation failure | Earlier snapshot/journal commits remain | Baseline/cycle count unchanged | Invariant error propagates |
| incident persistence failure | Incident transaction rolls back; earlier stages remain | Baseline/cycle count unchanged | Storage error propagates |
| SQLite write lock | No partial failed transaction | Baseline depends on failing stage and remains unchanged there | Bounded timeout, then visible `OperationalError` |
| SQLite corruption | No automatic repair or deletion | No fabricated continuation | SQLite/storage error remains visible |
| SIGTERM or SIGINT | Active synchronous stage may commit normally | Stop flag is idempotent; no next cycle | Active cycle finishes, handlers restore, lease releases |
| process crash | Already committed stage transactions survive | Volatile baseline is lost | Kernel releases lease; restart is conservative |
| second runtime | No state change | First runtime continues | Immediate bounded message and exit code 3 |

## Concurrency, lifecycle, and boundedness

The ownership lease covers scheduling waits and cycle execution but does not lock status, diagnosis, incident listing, or inspection. Those operations use independent short-lived SQLite connections and bounded reads. SQLite remains the authority for database read/write concurrency. Repeated lease release is safe. Collector subprocesses retain fixed argv, `shell=False`, minimal environment, combined output limits, deadlines, forced kill on exceptional paths, a final wait, selector closure, and pipe closure; Phase 11 introduces no subprocess.

A 500-cycle isolated runtime test verifies that no result list grows and only one sanitized two-source temporal baseline remains. Real SQLite tests cover restart without false resolution, durable journal cursor reuse, repeated-evidence idempotency, independent retention, and bounded lock visibility. Ownership tests cover simultaneous contenders, normal release, repeated release, symlink rejection, and abnormal `os._exit` recovery in a child interpreter. CLI tests cover ownership exit code, bounded private output, and signal-handler restoration.

## Files changed in Phase 11

```text
README.md
docs/architecture.md
docs/privacy.md
docs/phase11-validation.md
sentinel/application/runtime.py
sentinel/application/runtime_ownership.py
sentinel/main.py
tests/test_database.py
tests/test_runtime.py
tests/test_runtime_cli.py
tests/test_runtime_ownership.py
tests/test_runtime_reliability.py
```

## Known limitations

Ownership is local to Linux and the resolved database path; it is not a network-filesystem or distributed-host guarantee. An operator with permission to replace database/lock files can defeat local coordination. The runtime remains foreground-only and finishes rather than cancels an active synchronous cycle. Temporal continuity always resets after process restart. SQLite count retention is not a byte quota, encryption, or secure erasure. Process-to-service ownership is still unavailable, and transactions intentionally remain per application stage rather than falsely atomic across a complete cycle.

The complete `python3 -m unittest discover -s tests -v` regression passed all 193 tests, including the 500-cycle bounded-state test and real SQLite concurrency, restart, post-checkpoint failure, and incident-transaction rollback cases. Pytest is not installed and remains outside the dependency-free project contract. `python3 -m compileall -q sentinel` and `git diff --check` passed.

Live validation used a temporary SQLite database. One runtime completed ten one-second cycles while a concurrent second runtime failed immediately with the documented message and exit code 3; the first runtime continued to exit successfully. Snapshot count retention held the newest three records, the journal cursor progressed, inspection remained readable, and a later finite runtime reacquired ownership and completed. A separate process-level SIGTERM check delivered the signal during the five-second scheduler wait: the runtime exited 0 without stderr, and a subsequent runtime acquired the same state normally. `doctor`, `status`, JSON incident listing, and runtime schema-version-1 output all succeeded. The host exposed no service observations, so controlled SQLite integration tests—not the live host—prove incident opening, idempotency, and conservative non-resolution after restart.

No commit, push, tag, reset, rebase, amend, branch switch, or history rewrite was performed.
