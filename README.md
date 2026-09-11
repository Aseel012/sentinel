# Sentinel

Sentinel is a local-first Linux observability product. This repository currently implements Phase 1: trustworthy, typed observation of system, memory, CPU counters, processes, filesystem capacity, network counters, and optional systemd services.

It uses only the Python standard library, requires no root access, makes no network connections, and stores no data yet.

## Run

```bash
python3 -m sentinel status
python3 -m sentinel status --json
python3 -m sentinel doctor
```

`sentinel status --json` writes one compact JSON document to standard output. Its versioned contract starts with `"schema_version": "1"`, contains a `snapshot` with observations and aggregate warnings, and a `collectors` object containing each collector's status, timestamps, duration, error metadata, and warnings. Observations appear only in `snapshot`, avoiding duplicated process metadata. A degraded collector remains represented as structured JSON; an unavailable observation is `null`, never a fabricated zero.

Install it with `pipx install .` or `python3 -m pip install .` to expose the `sentinel` command. The supported runtime is Python 3.11+ on Linux.

## Scope

The source specification authorizes Phase 1 only. Persistence, daemon operation, derived rates, baselines, incident handling, and AI context are intentionally deferred so that observation semantics remain correct first. See [architecture](docs/architecture.md), [collectors](docs/collectors.md), and [privacy](docs/privacy.md).
