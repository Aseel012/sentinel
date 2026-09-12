# Sentinel

Sentinel is an evolving local-first Linux system intelligence foundation. It collects resource and service observations, remembers SQLite snapshots and journal events, compares history, and forms deterministic evidence-backed incident candidates.

It uses only the Python standard library, requires no root access, and makes no network connections. Persistence is explicit through application services; normal `status` remains read-only.

## Run

```bash
python3 -m sentinel status
python3 -m sentinel status --json
python3 -m sentinel doctor
python3 -m sentinel incidents
python3 -m sentinel incident <incident-id> --json
python3 -m sentinel diagnose <incident-id> --json
python3 -m sentinel run --cycles 1 --interval 1 --json
```

`sentinel status --json` writes one compact JSON document to standard output. Its versioned contract starts with `"schema_version": "1"`, contains a `snapshot` with observations and aggregate warnings, and a `collectors` object containing each collector's status, timestamps, duration, error metadata, and warnings. Observations appear only in `snapshot`, avoiding duplicated process metadata. A degraded collector remains represented as structured JSON; an unavailable observation is `null`, never a fabricated zero.

Install it with `pipx install .` or `python3 -m pip install .` to expose the `sentinel` command. The supported runtime is Python 3.11+ on Linux.

## Scope

Persistent storage is versioned, relational, and transactional. Pure comparisons derive counter rates and observed process/service lifecycle changes. Explicit journal ingestion uses bounded batches and durable cursors; ordinary `status` does not collect or persist journal messages.

Correlation records identity and temporal relationships. Deterministic diagnosis explains the strongest supported service-failure pattern, its provenance, limitations, and what remains unknown. Bounded incident inspection exposes remembered lifecycle, structured evidence, established and unknown facts, history, and the existing diagnosis without claiming current system state, causality, or root cause. The foreground `run` command composes collection, bounded persistence, journal checkpoints, temporal analysis, and incident reconciliation; finite `--cycles` makes operation testable, while omission runs until SIGINT or SIGTERM completes the active cycle. Anomaly scoring, alerting, notifications, AI agents, remediation, and daemon/service packaging are not implemented. See [architecture](docs/architecture.md), [collectors](docs/collectors.md), and [privacy](docs/privacy.md).

Run the regression suite with `python3 -m unittest discover -v` from the repository root.
