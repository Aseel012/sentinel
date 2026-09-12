# Phase 8 validation

Phase 8 was built on the uncommitted Phase 7 implementation without duplicating observation, identity, correlation, incident, SQLite, or retention logic. The starting Phase 1–7 baseline passed 144 tests plus compilation, whitespace, doctor, text-status, and JSON-status checks.

The diagnosis layer consumes `IncidentCandidate` and its bounded `EvidenceReference` values. It returns a typed state (`supported`, `insufficient_evidence`, or `unknown`), the selected deterministic rule, human explanation, supported fact codes, provenance references, historical incident IDs, inherited limitations, and facts explicitly not established. It does not inspect raw journal messages, process commands, executable names, PIDs, or similar service names.

The rules, in deterministic priority order, cover:

1. service failure + exact-unit journal event + explicitly associated process-lifetime termination;
2. service failure + exact-unit journal event;
3. service failure + explicitly associated process-lifetime termination;
4. service failure with insufficient diagnostic evidence;
5. unknown or unsupported structured service condition.

All supported explanations say the observations occurred together under existing identity and time rules. None claim that a process or journal event caused the failure. Quality limitations remain visible, and incomplete context is stated explicitly. Repeated diagnosis is equal for equal incident/history input. Up to 1,000 bounded history inputs are accepted and only the most recent 20 earlier candidates for the exact service identity are exposed.

Phase 7 summaries were intentionally human-readable and insufficient for trustworthy diagnosis. Schema v5 therefore adds a typed evidence fact: service failed/inactive/deactivating/removed, process exited/started/identity-changed, exact-unit journal event, or unknown. Existing v1–v4 databases migrate transactionally; old evidence becomes `unknown`. Reprocessing can enrich an unknown fact and union quality limitations without changing evidence identity. Conflicting known facts still fail atomically. Diagnoses themselves remain derived and are not persisted.

The minimum CLI is `sentinel diagnose <incident-id> [--database PATH] [--json]`. JSON has an explicit schema and structured provenance. Missing incidents return exit code 1 and a stable error code. Invalid IDs are rejected by argument parsing. Storage and programming errors remain visible rather than becoming empty diagnoses.

Security review found bounded models, explanations, provenance, history, and database reads; no network calls, telemetry, subprocesses, automatic commands, or blanket exception handling. Diagnosis output deliberately omits journal bodies, process commands, executable paths, and evidence summaries. Service names, cursors, lifetimes, and timestamps remain operationally sensitive. Regex redaction is still only a mitigation in upstream collection.

Focused coverage includes all diagnostic rules, insufficient/unknown evidence, partial and failed collection context, process nontermination/mismatch, exact-unit and time-window behavior inherited from correlation tests, duplicate/invariant rejection, deterministic ordering, repeated diagnosis, historical context, provenance, causal-language limits, schema migration, fact enrichment, quality union, CLI JSON, and missing incidents.

Final validation passed 157 tests. `python3 -m compileall -q sentinel` and `git diff --check` completed cleanly. Live `doctor`, text `status`, and JSON `status` checks succeeded; the public status JSON contract remained schema version `1` with its existing top-level keys.

Exact Phase 8 files changed on top of the Phase 7 working tree:

```text
README.md
docs/architecture.md
docs/privacy.md
docs/phase8-validation.md
sentinel/analysis/__init__.py
sentinel/analysis/correlation.py
sentinel/analysis/diagnosis.py
sentinel/application/diagnosis_service.py
sentinel/cli/diagnosis_output.py
sentinel/main.py
sentinel/models/__init__.py
sentinel/models/incidents.py
sentinel/storage/incidents.py
sentinel/storage/migrations.py
sentinel/storage/schema.py
tests/test_diagnosis.py
tests/test_diagnosis_cli.py
tests/test_correlation.py
tests/test_incident_storage.py
tests/test_migrations.py
```

The next appropriate phase is carefully bounded incident inspection and rule-based diagnostic breadth based on newly authoritative observations. Alerting, remediation, and AI should remain downstream consumers of these provenance contracts.

No commit, push, tag, reset, rebase, or history rewrite was performed.
