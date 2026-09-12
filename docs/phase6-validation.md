# Phase 6 implementation audit

Validated on 2026-09-12. This follow-up extends the existing implementation; it does not add another collector, database, comparison layer, or CLI family.

## Existing foundation and reuse

Before this follow-up, the repository already contained service and journal collectors, service/process/resource comparison functions, SQLite schema v2 events/checkpoints, redaction, and an explicit event application service. Snapshot persistence, connection permissions, transactional migrations, foreground sampling, and the status JSON contract were reused.

The explicit baseline suite passed 81 tests. The requested root command `python3 -m unittest discover -v` discovered zero tests; adding `tests/__init__.py` fixes that discovery gap. Compilation, whitespace checks, doctor, text status, and JSON status succeeded before edits.

## Correctness changes

- Services: validate current/previous/first-sample duplicates; expose invalid identity/state; prevent partial or invalid inventories from implying additions/removals; retain direct observed state changes. Service identity remains the canonical unit name.
- Events: replace tail-based paging with bounded oldest-first pages and inclusive cursor verification. Valid continuation drains bursts without skipping intermediate records. Identical timestamps/messages remain distinct when cursors differ; boot context is preserved.
- Bounds: one shared subprocess reader drains stdout and stderr under a combined byte budget and deadline. Journal defaults are 200 accepted events per poll, a configurable maximum of 1,000, a 2,000,000-byte combined output budget, a five-second deadline, 4,096-byte messages, 512-byte metadata, and 1,024-byte cursors. Service subprocess output has a 1,000,000-byte budget.
- Quality: permission failures, unavailable sources, invalid records, timeouts, truncation, and uncertain cursor continuity remain explicit. Per-event warnings and the latest collection outcome survive persistence. Older v2 facts receive `legacy_quality_unknown`.
- Storage: schema v3 migrates existing v1/v2 databases transactionally, adds event warnings/latest quality, and normalizes timestamp precision. Bounded retrieval round-trips event facts. An atomic expected-cursor check prevents stale concurrent writers from regressing the checkpoint.
- Retention: the existing retention module handles explicit event time cutoffs and a configurable default cap of 10,000 event rows on writes. Snapshot and event deletion are independent and preserve journal checkpoints. The count cap is not an exact disk-byte quota or secure erasure.

## Verification and limits

`python3 -m unittest discover -v` passes **114 tests**, including 33 additional regressions for lifecycle quality, bounded paging, event identity, boot changes, rotation, permission/encoding failures, subprocess limits, checkpoint races, retrieval, migration, and retention interaction. Real child-process tests exercise pipe draining and timeout cleanup. Read-only live journal checks successfully retrieved a bounded first page and resumed from its cursor without printing messages.

`python3 -m compileall -q sentinel`, `git diff --check`, `python3 -m sentinel doctor --json`, `python3 -m sentinel status`, and `python3 -m sentinel status --json` all pass. Parsed status JSON retains the baseline top-level, snapshot, collector keys, and schema version. Doctor still reports systemd/journal support and notifications `not_implemented`; actual service collection reports `unsupported` on this host, as at baseline. Executable availability does not establish manager access.

Regex redaction is only a mitigation: credentials and personal information may still be retained in permitted journal/command fields. Messages are not encrypted. Cursor invalidation and malformed/oversized source records preserve the checkpoint and require explicit recovery; there is no automatic skip or silent reset. Latest collection quality is not a complete history of all failed polls. Tests are focused evidence, not proof of every possible production condition. No static type checker or CI changes were added.

Sentinel can now preserve and compare observed service state and retain bounded, quality-aware journal context for later phases. It cannot infer root causes, prove restarts from samples, score anomalies, correlate services/processes, alert, notify, invoke AI agents, remediate, or run as a packaged production daemon. The tested storage, cursor, and quality contracts provide the foundation for that later work. No commit, push, tag, or history rewrite was performed.

## Exact files changed in this follow-up

Previously existing uncommitted process/temporal work was preserved. Paths below are relative to the repository root:

```text
README.md
docs/architecture.md
docs/collectors.md
docs/privacy.md
docs/phase6-validation.md
sentinel/analysis/services.py
sentinel/application/event_service.py
sentinel/collectors/journal.py
sentinel/collectors/services.py
sentinel/collectors/subprocesses.py
sentinel/models/__init__.py
sentinel/models/observations.py
sentinel/storage/__init__.py
sentinel/storage/events.py
sentinel/storage/migrations.py
sentinel/storage/retention.py
sentinel/storage/schema.py
tests/__init__.py
tests/test_database.py
tests/test_event_service.py
tests/test_events.py
tests/test_journal.py
tests/test_migrations.py
tests/test_services.py
```
