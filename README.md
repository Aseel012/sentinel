# Sentinel

Sentinel is a local-first Linux observability product. It currently provides trustworthy, typed Linux observation, persistent SQLite snapshots, and a reusable foreground sampling foundation.

It uses only the Python standard library, requires no root access, and makes no network connections. Persistence is explicit through application services; normal `status` remains read-only.

## Run

```bash
python3 -m sentinel status
python3 -m sentinel status --json
python3 -m sentinel doctor
```

`sentinel status --json` writes one compact JSON document to standard output. Its versioned contract starts with `"schema_version": "1"`, contains a `snapshot` with observations and aggregate warnings, and a `collectors` object containing each collector's status, timestamps, duration, error metadata, and warnings. Observations appear only in `snapshot`, avoiding duplicated process metadata. A degraded collector remains represented as structured JSON; an unavailable observation is `null`, never a fabricated zero.

Install it with `pipx install .` or `python3 -m pip install .` to expose the `sentinel` command. The supported runtime is Python 3.11+ on Linux.

## Scope

Persistent storage is versioned, relational, and transactional. Daemon operation, derived rates, baselines, incident handling, and AI context remain deferred. See [architecture](docs/architecture.md), [collectors](docs/collectors.md), and [privacy](docs/privacy.md).
