# Phase 10 validation

Phase 10 makes the Phase 1–9 pipeline operational through one bounded foreground runtime. Three independent agent audits reviewed architecture reuse, test design, and concurrency/security risks before integration. The resulting design adds orchestration rather than replacing collectors, persistence, temporal analysis, correlation, incidents, diagnosis, or inspection.

## Runtime responsibility and cycle

`ContinuousObservationRuntime` implements the recorder interface already accepted by `Sampler`. A completed cycle:

1. captures a monotonic start;
2. invokes `PersistentSnapshotService` to collect and transactionally store one snapshot;
3. invokes `JournalEventService`, preserving its durable cursor and quality outcome;
4. compares the current and prior completed collection through `compare_service_collections` and `compare_process_collections`;
5. timestamps each fact with its exact source collection UTC timestamp;
6. loads a bounded persisted exact-unit event window for authoritative service anchors;
7. delegates correlation and lifecycle reconciliation to `IncidentFormationService` with no inferred process ownership;
8. emits one immutable, privacy-bounded `RuntimeCycle` and replaces the single prior-collection baseline.

The persisted event query is important: an event ingested during one cycle can support a service transition first observed in the next cycle. It reuses `load_event_window` and the correlation module's authoritative anchor predicate, so it does not duplicate failure rules or scan raw messages.

## Timing, restart, and failure semantics

Scheduling, cycle durations, and process elapsed time use an injected monotonic clock. Persisted/source timestamps remain UTC and are never used to manufacture elapsed rates. Intervals are bounded from 0.1 to 86,400 seconds. Finite runs accept 1 through 100,000 cycles; omitting `--cycles` runs in the foreground until stopped. SIGINT and SIGTERM set the existing sampler stop event, allow the active synchronous cycle to finish, and restore prior handlers.

Only one prior completed collection and its monotonic point live in memory. A fresh process intentionally starts with insufficient temporal history, even when snapshots exist, because monotonic elapsed state cannot survive restart. Journal checkpoints do survive and are never casually reset. No raw event/process cache or cycle-result list accumulates.

Snapshot, journal, and incident services keep separate transactions. There is no false all-or-nothing cycle claim: if a later stage fails, its exception surfaces and earlier committed state remains valid. Runtime temporal state is not advanced and no completed result is emitted. Expected Linux collection failures remain structured statuses. Partial/failed process or service inventories therefore cannot prove exits, removals, or resolution; only complete explicit recovery retains the Phase 7 resolution authority.

## Bounds and directly relevant corrections

No schema migration was needed. Runtime snapshots now use an optional count cap in `SnapshotRepository.save`; `sentinel run` defaults to the newest 10,000 and allows 1 through 100,000. Trimming occurs inside the snapshot transaction and cascades child observations. Existing non-runtime callers preserve their prior no-policy default.

The audit found that `/proc` collection previously materialized every numeric entry. It now selects the lowest PIDs deterministically with bounded heap memory, defaults to 10,000 observations, caps configuration at 100,000, and marks truncation `partial`. This prevents truncated inventory from manufacturing exits. The journal application service now rejects an injected batch over the collector's 1,000-event hard cap before checkpoint persistence. Persisted correlation reads are bounded and expose `correlation_event_window_truncated` rather than hiding incomplete evidence.

## CLI and output

```text
sentinel run [--database PATH] [--interval SECONDS] [--cycles N]
             [--event-limit N] [--snapshot-limit N] [--json]
```

Each text line or JSON line describes one completed cycle. JSON schema version `1` contains monotonic/UTC timing, snapshot ID, per-source status, bounded observation counts, accepted and correlated event counts, candidate/reconciled/resolved incident IDs, and limitation codes. It never labels candidates as newly created because the repository contract reports idempotent reconciliation, not insert-versus-update. Multiple JSON cycles are newline-delimited documents for streaming rather than one growing array.

Output excludes raw journal messages, process commands, executable paths, raw collector warnings/errors, environment data, severity, confidence, and causal claims. Status, diagnosis, and inspection JSON contracts remain unchanged.

## Tests and validation

Phase 10 tests cover finite and multiple cycles, timing/configuration bounds, deterministic scheduling and output, partial/failed sources, exact source statuses/timestamps, journal cursor reuse, prior-cycle event correlation, bounded exact-unit event lookup, process and event hard caps, snapshot count retention, persistence and incident invocation, opening and authoritative resolution through real SQLite services, idempotent identities, graceful stop, signal handler restoration, storage/invariant failure visibility, minimal runtime state, and raw-data exclusion.

The complete standard-library regression passed all 182 tests. `python3 -m pytest` was also attempted, but the interpreter reports `No module named pytest`; pytest is not a declared project dependency, so it was not added solely to duplicate the authoritative `unittest` suite. `python3 -m compileall -q sentinel` and `git diff --check` passed.

A finite live smoke run used a temporary SQLite database with one 0.1-second cycle, a 10-event input limit, a five-snapshot retention cap, and JSON output. It exited successfully, persisted one schema-version-5 snapshot and ten bounded events, and produced a schema-version-1 runtime document. `doctor`, text `status`, JSON `status`, and incident listing also exited successfully; the existing status JSON top-level keys and schema version remained unchanged. The host reports service collection as unsupported and journal collection as partial, so the live smoke test formed no incident. The controlled real-SQLite integration test separately proves deterministic candidate opening and authoritative recovery resolution.

## Phase 10 files

```text
README.md
docs/architecture.md
docs/privacy.md
docs/phase10-validation.md
sentinel/analysis/__init__.py
sentinel/analysis/correlation.py
sentinel/application/event_query_service.py
sentinel/application/event_service.py
sentinel/application/persistence_service.py
sentinel/application/runtime.py
sentinel/application/runtime_models.py
sentinel/cli/runtime_output.py
sentinel/collectors/processes.py
sentinel/main.py
sentinel/storage/retention.py
sentinel/storage/snapshots.py
tests/test_collectors.py
tests/test_event_query_service.py
tests/test_event_service.py
tests/test_runtime.py
tests/test_runtime_cli.py
```

Known limitations: the runtime is foreground-only; first-cycle temporal facts reset conservatively after restart; process/service ownership remains unavailable; snapshot retention is count-based rather than a disk-byte quota; and separate concurrent runtime processes are not prevented by a global lease. No daemon installation, background service, notification, alerting, remediation, network client, AI/ML, severity, or root-cause inference was added.

No commit, push, tag, reset, rebase, branch change, or history rewrite was performed.
