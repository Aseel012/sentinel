# Phase 7 validation

Phase 7 extends the Phase 6 repository in place. Existing observation models, process lifetime identity, canonical service identity, temporal analysis, journal cursors, collection quality, SQLite connections, transactions, migrations, event indexes, and retention primitives were reused.

The design follows the sequence **observation → temporal fact → evidence reference → incident candidate**. Evidence records why facts relate; it does not replace the underlying observation or assert a cause. Candidates require a degraded/removal service anchor plus an exact-unit journal event or an explicitly supplied service/process-lifetime association inside an inclusive UTC window. Similar names, equal timestamps/messages, event PIDs, and PIDs without lifetime identity do not correlate.

Shared immutable models define typed subjects, evidence kinds/relations, active/resolved lifecycle, stable identifiers, UTC timestamps, one opening anchor, at least two evidence kinds, and bounded deterministic limitations. Pure correlation creates a stable versioned SHA-256 incident ID from the opening service fact. Input iterables and evidence are bounded; sorting is explicit. Journal text and process commands are never copied into incidents.

Schema v4 adds normalized `incidents`, `incident_evidence`, `incident_limitations`, and `incident_evidence_limitations` tables. The migration is transactional and preserves schema v1–v3 data. `IncidentRepository` performs idempotent reconciliation in one transaction, rejects semantic identity collisions, merges evidence with a 128-reference cap while retaining the anchor, and provides bounded deterministic reads. Only a complete service collection with an explicit transition back to active can resolve an incident. Resolved-incident retention cascades its derived evidence; snapshot and event retention remain independent.

`IncidentFormationService` carries service/process/journal quality into correlation and coordinates persistence. Failed collections cannot supply temporal facts. Partial collections can contribute facts they actually observed and add quality limitations, but they cannot resolve an incident. A bounded indexed event-window query reports whether further matching events were omitted.

Security and privacy review confirmed no network access, telemetry, shell interpolation, raw journal-message copying, command copying, broad exception swallowing, or implicit process ownership. Inputs, identifiers, summaries, limitations, time windows, queries, and stored evidence are bounded. SQLite remains owner-only and local. Operational identifiers still carry sensitive context, regex redaction remains imperfect, and retention is not secure erasure.

Sentinel can now deterministically say that identified service, event, and explicitly associated process facts occurred together under inspectable rules and maintain one incident candidate across repeated processing. It cannot determine root cause, discover systemd process ownership, diagnose, score anomalies, correlate system-wide resource changes, alert, notify, remediate, invoke AI, or run as a packaged daemon.

The recommended next phase is diagnosis over these stable incident/evidence contracts, beginning with explicit rule-based explanations and provenance. Alerting and AI should remain later consumers.

Validation completed with `python3 -m unittest discover -v`: **144 tests passed** (114 at the Phase 6 baseline). `python3 -m compileall -q sentinel` and `git diff --check` passed. `python3 -m sentinel doctor --json`, text `status`, and JSON `status` passed; the parsed status response retains the same top-level, snapshot, collector keys, and public schema version as the pre-Phase-7 baseline. No type-checking or CI configuration was added, and no claim of complete static type verification is made.

Exact Phase 7 files changed relative to commit `fc787b1`:

```text
README.md
docs/architecture.md
docs/privacy.md
docs/phase7-validation.md
sentinel/analysis/__init__.py
sentinel/analysis/correlation.py
sentinel/application/incident_service.py
sentinel/models/__init__.py
sentinel/models/incidents.py
sentinel/storage/__init__.py
sentinel/storage/events.py
sentinel/storage/incidents.py
sentinel/storage/migrations.py
sentinel/storage/retention.py
sentinel/storage/schema.py
tests/test_correlation.py
tests/test_events.py
tests/test_incident_service.py
tests/test_incident_storage.py
tests/test_migrations.py
```

No commit, push, tag, reset, rebase, or history rewrite was performed.
