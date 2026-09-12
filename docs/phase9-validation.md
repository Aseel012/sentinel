# Phase 9 validation

Phase 9 adds bounded incident inspection on top of the uncommitted Phase 1–8 working tree. It is a derived application and presentation layer: no incident, evidence, diagnosis, identity, timestamp, retention, or database concept was duplicated, and no schema migration was required.

## Architecture reuse

`IncidentInspectionService` uses `IncidentRepository.list_recent()` for the plural view and `IncidentRepository.get()` for detail. The existing default bound is 100 and the hard maximum is 1,000. Ordering remains the repository's explicit newest-first `last_observed_at`, then incident-ID order. Optional `active`/`resolved` filtering uses the persisted lifecycle enum. Single inspection calls `DiagnosisService.derive()`, which centralizes the same bounded subject-history policy used by `sentinel diagnose`.

The immutable `IncidentInspection` pair verifies that incident, diagnosis, and subject identities agree. Inspection does not reread Linux, use wall-clock time, infer current state, recompute lifecycle, or persist derived presentation state.

## CLI and JSON

Phase 9 adds:

```text
sentinel incidents [--limit N] [--state active|resolved] [--database PATH] [--json]
sentinel incident <incident-id> [--database PATH] [--json]
```

List output includes incident ID, stored lifecycle state, service identity, opening timestamp, and an actual resolution timestamp or an explicit absent value. It states the applied query limit. JSON reports `limit_reached` when the returned count reaches that bound, avoiding an unsupported claim that the database has no additional rows.

Detail output includes stored lifecycle timestamps, ordered evidence identifiers, kinds, typed facts, correlation relationships, per-evidence quality limitations, established facts, unknown evidence identities, related historical incident IDs, incident and diagnosis limitations, and the existing deterministic diagnosis. It explicitly states that historical lifecycle does not establish current system state. The embedded diagnosis uses the exact serializer used by `sentinel diagnose`, preventing a second diagnosis contract.

The incident-inspection JSON contract is schema version `1` and is separate from status JSON. Existing `status` and `diagnose` commands and their schemas remain unchanged. Invalid IDs are rejected by argument parsing. Missing valid IDs return exit code 1 and the stable `incident_not_found` error. SQLite, filesystem, corruption, and invariant failures remain visible.

## Determinism, boundedness, and privacy

All reads reuse existing bounds. Each incident's evidence and limitations retain model/storage caps, history accepts at most 1,000 candidates and exposes at most 20 related IDs, and output preserves deterministic tuple or sorted ordering. Repeated inspection over unchanged storage produces identical output.

Neither text nor JSON emits evidence summaries, journal messages, process commands, or executable paths. No network, telemetry, shell command, external API, AI/ML, current-state collection, severity, ownership inference, causal inference, alerting, notification, or remediation path was added. Structured service names, event cursors, process lifetime IDs, and timestamps remain operationally sensitive.

## Tests and regression

Phase 9 tests cover empty and multiple lists, bounds, deterministic ordering, lifecycle filtering, active and resolved incidents, single inspection, missing and invalid IDs, storage failure visibility, evidence ordering, typed and unknown facts, inherited limitations, historical context, exact diagnosis serializer reuse, JSON output, deterministic repeated text, current-state and causal disclaimers, and exclusion of raw source content.

The targeted inspection/diagnosis suite passed 21 tests and the final complete repository regression passed 167 tests. `python3 -m compileall -q sentinel` and `git diff --check` passed. Manual execution against a real temporary incident exercised plural text listing, detail text, detail JSON, the unchanged dedicated diagnosis command, and stable missing-incident exit code 1. Invalid IDs produced argument exit code 2. Live doctor and status checks passed, and status JSON retained schema version `1` with exactly its existing `schema_version`, `snapshot`, and `collectors` top-level keys. A focused source scan found no blanket exception handler, network client, shell execution, or subprocess path in the inspection layer.

## Phase 9 files

```text
README.md
docs/architecture.md
docs/privacy.md
docs/phase9-validation.md
sentinel/application/diagnosis_service.py
sentinel/application/inspection_service.py
sentinel/cli/diagnosis_output.py
sentinel/cli/incident_output.py
sentinel/main.py
tests/test_incident_cli.py
tests/test_inspection_service.py
```

No commit, push, tag, reset, rebase, or history rewrite was performed.
