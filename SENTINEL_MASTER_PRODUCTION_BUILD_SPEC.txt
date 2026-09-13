# SENTINEL — MASTER PRODUCTION BUILD SPECIFICATION
## Local-first Linux System Observability, Historical Context, Diagnosis and AI-Agent Context Layer

**Document status:** Master engineering protocol  
**Purpose:** Canonical specification for building Sentinel as a real product, not a prototype  
**Implementation model:** Multi-agent / Codex-assisted engineering with explicit ownership and review gates  
**Primary target:** Linux desktop and server systems  
**Primary runtime:** Python 3  
**Primary persistence:** SQLite  
**Core principle:** Observe the machine continuously, preserve trustworthy history, derive evidence, correlate it, and expose machine context without making an LLM responsible for core correctness.

---

# 1. PRODUCT DEFINITION

Sentinel is a local-first Linux observability product.

Its central job is not to display more metrics than `top`, `htop`, `free`, `df`, `ip`, or `systemctl`.

Its central job is to build a persistent, structured understanding of one machine over time.

The product pipeline is:

```text
Linux machine
    ↓
observe
    ↓
normalize
    ↓
validate
    ↓
snapshot
    ↓
persist
    ↓
compare with history
    ↓
derive rates
    ↓
build baseline
    ↓
detect anomalies
    ↓
correlate evidence
    ↓
diagnose
    ↓
open / update / recover incidents
    ↓
explain
    ↓
present to human or AI agent
```

The differentiator is longitudinal machine context.

An agent that is asked a question can inspect a machine at that moment.

Sentinel continuously remembers what has been happening.

That distinction is the foundation of the product.

---

# 2. NON-GOALS

Sentinel is not initially:

- a Prometheus replacement
- a full SIEM
- an antivirus product
- an EDR
- a packet sniffer
- a cloud observability platform
- a Kubernetes control plane
- a general remote fleet-management platform
- an LLM chatbot
- an autonomous shell agent
- an automatic remediation daemon
- a dashboard-first project

Future integrations are possible, but they must not contaminate the core architecture.

---

# 3. ENGINEERING REFERENCES

The project must use the following books as conceptual references.

## 3.1 Operating Systems: Three Easy Pieces

Use OSTEP for:

- processes
- CPU scheduling concepts
- CPU time
- memory concepts
- virtualization
- concurrency
- persistence
- I/O
- resource behavior
- why operating-system state is time-dependent

Do not reduce an operating-system concept to a single "current percentage" if the underlying information is cumulative or historical.

---

## 3.2 The Linux Programming Interface

Use TLPI for:

- `/proc`
- process lifetime
- process IDs
- files and file descriptors
- permissions
- signals
- subprocesses
- `/sys`
- Linux interfaces
- system calls and their semantics
- process races
- system behavior while the observer is running

Sentinel must assume that the machine changes while it is being observed.

---

## 3.3 Python Cookbook

Use the Python Cookbook for:

- dataclasses
- context managers
- iterators/generators where appropriate
- subprocess execution
- parsing
- resource management
- clean standard-library implementations
- exception handling
- practical Python patterns
- avoiding needless dependencies

Do not add a class hierarchy merely because a large system "looks more professional".

Professional means the abstractions are justified.

---

## 3.4 Designing Data-Intensive Applications

Use DDIA for:

- local persistent data design
- schema design
- indexes
- transactions
- retention
- historical queries
- storage growth
- schema evolution
- consistency
- data lifecycle

SQLite is the initial storage engine because Sentinel is local-first and single-machine by design.

---

## 3.5 Google SRE and SRE Workbook

Use SRE principles for:

- observability
- signal quality
- alert fatigue
- baselines
- incidents
- severity
- evidence
- diagnosis
- recovery
- operational correctness
- avoiding noisy alerts
- treating monitoring itself as an operational system

A metric crossing a threshold is not automatically an incident.

---

# 4. ARCHITECTURAL LAWS

These rules are mandatory.

## 4.1 Collectors do not diagnose

Collectors read Linux state.

They return structured observations.

They do not say:

```text
"memory leak"
```

They may only report:

```text
process RSS = 4.2 GiB
```

---

## 4.2 Models do not access the machine

A model is data.

It does not open `/proc`.

It does not run `systemctl`.

It does not write SQLite.

---

## 4.3 Storage does not analyze

SQLite repositories persist and query.

They do not decide whether behavior is abnormal.

---

## 4.4 Analysis does not print

Analysis creates structured results.

CLI formatting is separate.

---

## 4.5 CLI does not inspect Linux directly

CLI commands call application services.

They do not contain `/proc` parsing or SQL.

---

## 4.6 AI does not own correctness

LLMs may explain evidence.

The LLM is not the source of truth for:

- CPU calculations
- memory calculations
- timestamps
- persistence
- incident state
- threshold logic
- data validity

---

# 5. FINAL SYSTEM ARCHITECTURE

```text
                                  ┌───────────────────────────┐
                                  │          LINUX            │
                                  │                           │
                                  │ /proc  /sys  filesystem   │
                                  │ systemd  journal  OS      │
                                  └────────────┬──────────────┘
                                               │
                                               ▼
                                  ┌───────────────────────────┐
                                  │      PLATFORM LAYER        │
                                  │                           │
                                  │ capabilities              │
                                  │ Linux interface adapters  │
                                  │ environment detection     │
                                  └────────────┬──────────────┘
                                               │
                                               ▼
                                  ┌───────────────────────────┐
                                  │        COLLECTORS          │
                                  │                           │
                                  │ memory                    │
                                  │ CPU                       │
                                  │ system                    │
                                  │ processes                 │
                                  │ disk                      │
                                  │ network                   │
                                  │ services                  │
                                  │ journal/events            │
                                  └────────────┬──────────────┘
                                               │
                                               ▼
                                  ┌───────────────────────────┐
                                  │     DOMAIN MODELS          │
                                  │                           │
                                  │ observations              │
                                  │ collection status         │
                                  │ snapshots                │
                                  │ evidence                  │
                                  │ anomalies                │
                                  │ incidents                │
                                  └────────────┬──────────────┘
                                               │
                                  ┌────────────┴────────────┐
                                  │                         │
                                  ▼                         ▼
                       ┌────────────────────┐    ┌────────────────────┐
                       │      STORAGE       │    │ APPLICATION LAYER  │
                       │                    │    │                    │
                       │ SQLite             │    │ orchestration      │
                       │ migrations         │    │ monitor service    │
                       │ repositories       │    │ history service    │
                       │ retention          │    │ incident service   │
                       └──────────┬─────────┘    └──────────┬─────────┘
                                  │                         │
                                  └────────────┬────────────┘
                                               ▼
                                  ┌───────────────────────────┐
                                  │          ANALYSIS          │
                                  │                           │
                                  │ rates/deltas              │
                                  │ thresholds               │
                                  │ baselines                │
                                  │ anomalies                │
                                  │ correlation              │
                                  │ diagnosis                │
                                  │ incident lifecycle       │
                                  └────────────┬──────────────┘
                                               │
                                               ▼
                                  ┌───────────────────────────┐
                                  │       USER INTERFACE       │
                                  │                           │
                                  │ CLI                       │
                                  │ JSON interface            │
                                  │ desktop notifications     │
                                  │ daemon status             │
                                  └────────────┬──────────────┘
                                               │
                                  ┌────────────┴────────────┐
                                  ▼                         ▼
                               HUMAN                    AI AGENT
```

---

# 6. REPOSITORY STRUCTURE

The structure can grow, but responsibilities must remain clear.

```text
sentinel/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
│
├── docs/
│   ├── architecture.md
│   ├── installation.md
│   ├── configuration.md
│   ├── privacy.md
│   ├── troubleshooting.md
│   ├── collectors.md
│   ├── storage.md
│   ├── analysis.md
│   ├── incidents.md
│   ├── ai-interface.md
│   └── release.md
│
├── sentinel/
│   ├── __init__.py
│   ├── __main__.py
│   ├── main.py
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── defaults.py
│   │   ├── loader.py
│   │   └── models.py
│   │
│   ├── platform/
│   │   ├── __init__.py
│   │   ├── detection.py
│   │   ├── capabilities.py
│   │   └── linux/
│   │       ├── __init__.py
│   │       ├── proc.py
│   │       ├── sysfs.py
│   │       ├── systemd.py
│   │       └── journal.py
│   │
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── memory.py
│   │   ├── cpu.py
│   │   ├── system.py
│   │   ├── processes.py
│   │   ├── disk.py
│   │   ├── network.py
│   │   ├── services.py
│   │   └── journal.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── common.py
│   │   ├── memory.py
│   │   ├── cpu.py
│   │   ├── system.py
│   │   ├── process.py
│   │   ├── disk.py
│   │   ├── network.py
│   │   ├── service.py
│   │   ├── event.py
│   │   ├── evidence.py
│   │   ├── anomaly.py
│   │   ├── incident.py
│   │   └── snapshot.py
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   ├── migrations.py
│   │   ├── schema.py
│   │   ├── snapshots.py
│   │   ├── processes.py
│   │   ├── services.py
│   │   ├── events.py
│   │   ├── anomalies.py
│   │   ├── incidents.py
│   │   └── retention.py
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── rates.py
│   │   ├── thresholds.py
│   │   ├── baseline.py
│   │   ├── anomalies.py
│   │   ├── correlation.py
│   │   ├── diagnosis.py
│   │   └── incidents.py
│   │
│   ├── application/
│   │   ├── __init__.py
│   │   ├── snapshot_service.py
│   │   ├── monitoring_service.py
│   │   ├── history_service.py
│   │   ├── analysis_service.py
│   │   ├── diagnosis_service.py
│   │   └── incident_service.py
│   │
│   ├── daemon/
│   │   ├── __init__.py
│   │   ├── runner.py
│   │   ├── scheduler.py
│   │   └── lifecycle.py
│   │
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── desktop.py
│   │   └── manager.py
│   │
│   ├── interfaces/
│   │   ├── __init__.py
│   │   ├── json.py
│   │   └── agent.py
│   │
│   └── cli/
│       ├── __init__.py
│       ├── commands.py
│       ├── formatters.py
│       └── output.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   ├── collectors/
│   ├── models/
│   ├── storage/
│   ├── analysis/
│   ├── application/
│   ├── daemon/
│   └── cli/
│
├── packaging/
│   ├── systemd/
│   │   └── sentinel.service
│   ├── desktop/
│   │   └── sentinel.desktop
│   └── scripts/
│       ├── install.sh
│       └── uninstall.sh
│
└── tools/
    ├── development/
    └── diagnostics/
```

The earlier small tree is still the Phase 1 entry point. The additional structure appears as each responsibility becomes real.

---

# 7. PHASE ROADMAP

## Phase 0 — Contract and skeleton

Goal:

- package identity
- pyproject
- source tree
- architecture documentation
- testing foundation
- CLI entry point
- versioning strategy

No monitoring intelligence.

---

## Phase 1 — Observation foundation

Build:

- platform detection
- capabilities
- memory
- CPU counters
- system
- processes
- disk/filesystem
- network counters
- services where available
- typed models
- SystemSnapshot
- collection result/status
- collection orchestrator
- unit/integration fixtures

No database.
No baseline.
No anomaly engine.
No incident engine.

---

## Phase 2 — Persistence

Build:

- SQLite
- migrations
- repositories
- snapshot persistence
- process persistence
- service persistence
- events
- database health
- retention
- backup/export foundation

---

## Phase 3 — Continuous daemon

Build:

- scheduler
- monotonic scheduling
- graceful shutdown
- signals
- single-instance protection
- systemd user service
- self-health
- sampling history

---

## Phase 4 — Derived rates

Build:

- CPU utilization
- process CPU utilization
- network throughput
- disk throughput where supported
- memory growth
- sampling gaps
- counter-reset handling
- reboot detection

---

## Phase 5 — Process intelligence

Build:

- process lifetime identity
- PID reuse protection
- process tree
- parent/child relationships
- process start time
- command metadata
- process resource history
- service ownership where available

---

## Phase 6 — Services and events

Build:

- systemd transitions
- service restart behavior
- journal event collection
- bounded event ingestion
- event timestamps vs collection timestamps
- event correlation window foundation

---

## Phase 7 — Baselines

Build:

- machine-specific baseline
- time-aware baseline
- sufficient-history logic
- baseline quality/confidence
- robust statistics where justified
- baseline reset/rebuild behavior

---

## Phase 8 — Anomalies

Build:

- absolute thresholds
- rate anomalies
- baseline deviations
- persistence
- context-aware anomaly scoring
- anomaly lifecycle

---

## Phase 9 — Correlation

Build:

- temporal correlation
- resource → process
- process → service
- service → event
- event → incident
- historical similarity
- evidence ranking

---

## Phase 10 — Diagnosis

Build:

- structured diagnosis
- candidate causes
- confidence
- limitations
- missing evidence
- next useful observation

No false certainty.

---

## Phase 11 — Incidents

Build:

- incident identity
- deduplication
- severity
- lifecycle
- evidence references
- recovery
- flapping detection
- notification eligibility

---

## Phase 12 — Product interface

Build:

- status
- watch
- top
- history
- diff
- process
- services
- incidents
- diagnose
- report
- doctor
- config
- version
- export/backup

---

## Phase 13 — AI interface

Build:

- stable JSON schema
- compact context export
- incident context
- evidence summaries
- machine state context
- historical context
- limitations

No requirement for a hosted LLM.

---

## Phase 14 — Distribution

Build:

- installation route
- systemd integration
- desktop notification integration
- upgrade behavior
- uninstall
- documentation
- release archive
- smoke tests

---

# 8. PHASE 1 DETAILED CONTRACT

Phase 1 is foundational and must be stronger than the earlier prototype-style collectors.

Every collector should return either a valid observation or a structured result describing why collection was partial/unavailable.

A conceptual result:

```python
@dataclass(frozen=True)
class CollectionResult[T]:
    value: T | None
    status: CollectionStatus
    collected_at: datetime
    duration_seconds: float
    error_code: str | None
    error_message: str | None
    warnings: tuple[str, ...]
```

The exact type design can change if an equivalent design is better for the supported Python version.

The required semantics cannot change:

```textsuccess
partial
permission denied
unsupported
transient failure
invalid data
```

An empty list must never mean both:

```text"there are zero services"
```

and:

```text"service inspection failed"
```

---

# 9. PHASE 1 MODELS

## MemoryObservation

Suggested fields:

```python
@dataclass(frozen=True)
class MemoryObservation:
    total_bytes: int
    available_bytes: int
    free_bytes: int
    buffers_bytes: int | None
    cached_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None
```

Use Linux semantics carefully.

Do not use `MemFree` alone as a memory-health decision.

---

## CPUObservation

Raw cumulative counters:

```python
@dataclass(frozen=True)
class CPUObservation:
    user_ticks: int
    nice_ticks: int
    system_ticks: int
    idle_ticks: int
    iowait_ticks: int
    irq_ticks: int
    softirq_ticks: int
    steal_ticks: int
```

One sample is not CPU utilization.

---

## SystemObservation

```python
@dataclass(frozen=True)
class SystemObservation:
    hostname: str
    operating_system: str
    kernel: str
    architecture: str
    uptime_seconds: float
```

---

## ProcessObservation

Production-level process history must eventually include:

```python
@dataclass(frozen=True)
class ProcessObservation:
    pid: int
    ppid: int
    name: str
    state: str
    rss_bytes: int
    virtual_memory_bytes: int | None
    threads: int
    cpu_time_ticks: int | None
    start_time_ticks: int | None
    command: str
    executable: str | None
```

Do not turn permission failures into zero.

Unknown is different from zero.

---

## DiskObservation

```python
@dataclass(frozen=True)
class DiskObservation:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
```

Filesystem capacity and disk I/O are separate concepts.

---

## NetworkObservation

```python
@dataclass(frozen=True)
class NetworkObservation:
    interface: str
    receive_bytes: int
    transmit_bytes: int
    receive_packets: int | None
    transmit_packets: int | None
    receive_errors: int | None
    transmit_errors: int | None
    receive_drops: int | None
    transmit_drops: int | None
```

Counters now. Rates later.

---

## ServiceObservation

```python
@dataclass(frozen=True)
class ServiceObservation:
    name: str
    load_state: str
    active_state: str
    sub_state: str
```

---

## SystemSnapshot

```python
@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: datetime
    system: SystemObservation
    memory: MemoryObservation
    cpu: CPUObservation
    processes: tuple[ProcessObservation, ...]
    disk: DiskObservation
    network: tuple[NetworkObservation, ...]
    services: tuple[ServiceObservation, ...]
```

Internal timestamps should use UTC.

Local timezone formatting belongs in presentation.

---

# 10. TIME MODEL

Two clocks are required conceptually.

## Wall clock

Use for:

- persisted timestamps
- event timestamps
- user-facing time
- incident start/recovery

Use UTC internally.

## Monotonic clock

Use for:

- elapsed duration
- scheduling
- sample interval
- collector duration
- rate calculations inside one running process

Never assume wall-clock subtraction remains monotonic.

---

# 11. NON-OBVIOUS PROCESS IDENTITY RULE

PID is not a permanent identity.

Example:

```textPID 5000 = process A
process A exits
PID 5000 reused by process B
```

If history uses only PID, Sentinel can attribute B's behavior to A.

Use a process lifetime identity based on:

```textPID + process start time
```

or an equivalent reliable strategy.

This is mandatory before persistent process history is trusted.

---

# 12. RACE HANDLING

Linux is changing while Sentinel reads it.

Expected races include:

```textprocess disappears
network interface disappears
service exits
file becomes unreadable
permission changes
```

Collectors must isolate these failures.

One disappearing process must not invalidate the entire system snapshot.

But the snapshot quality must record that the process set was incomplete.

---

# 13. COUNTER SEMANTICS

Raw counters can:

- increase normally
- reset
- wrap at an external boundary
- disappear
- change epoch after reboot/restart

When:

```textcurrent < previous
```

do not calculate a negative rate.

Mark a counter reset and start a new comparison window.

---

# 14. CPU RATE FORMULA

For two samples:

```textdelta_total = total_B - total_A
delta_idle = idle_B - idle_A
busy = delta_total - delta_idle

utilization = busy / delta_total
```

Require:

```textdelta_total > 0
```

Otherwise report unknown.

The formula must be unit-tested.

---

# 15. NETWORK RATE FORMULA

Given:

```textbytes_A
bytes_B
elapsed_seconds
```

calculate:

```textdelta = bytes_B - bytes_A
rate = delta / elapsed_seconds
```

If the counter decreased:

```textcounter reset
```

not:

```textnegative throughput
```

---

# 16. MEMORY PRESSURE MODEL

A useful memory model should consider:

```textavailable memory
swap behavior
recent memory growth
process RSS growth
historical normal range
```

Do not classify memory pressure using only low free memory.

Linux aggressively uses RAM for useful caching.

---

# 17. PROCESS HISTORY

Eventually persist:

```textprocess lifetime identity
PID
PPID
name
start time
resource samples
command metadata where allowed
```

Do not treat:

```textPID = 1234
```

as sufficient identity.

---

# 18. SYSTEMD IS OPTIONAL

Sentinel must detect systemd capability.

If not present:

```textservice collector = unsupported
```

while core monitoring continues.

Do not make systemd a hard dependency for:

```textmemory
CPU
processes
disk
network
```

---

# 19. DATABASE ARCHITECTURE

Default:

```text~/.local/share/sentinel/sentinel.db
```

Potential tables:

```textschema_migrations
snapshots
memory_observations
cpu_observations
process_observations
network_observations
disk_observations
service_observations
events
anomalies
incidents
incident_evidence
```

Use deliberate schemas.

Do not store pickled Python objects.

Do not store arbitrary serialized application objects as the source of truth.

---

# 20. DATABASE SAFETY

Require:

- schema version
- migrations
- foreign keys
- transactions
- rollback
- indexes
- retention
- health check
- corruption/error handling
- safe connection lifecycle

Do not put one permanent mutable SQLite connection in a random global.

---

# 21. RETENTION

Raw observations can grow rapidly.

The design should eventually separate:

```textraw samples
aggregates
incidents
```

Example policy:

```textraw: 7–30 days
aggregates: 90–365 days
incidents: long-term
```

These are defaults to evaluate and benchmark, not immutable product promises.

Retention must be configurable.

---

# 22. SNAPSHOT VERSUS EVENT VERSUS ANOMALY VERSUS INCIDENT

These are distinct.

### Observation

```textavailable RAM = 1.1 GiB
```

### Snapshot

```textall current observations collected around timestamp T
```

### Event

```textservice restarted
```

### Anomaly

```textmemory is far outside normal behavior
```

### Diagnosis

```textlikely sustained memory pressure
```

### Incident

```texttracked condition with start, status, evidence and recovery
```

Never collapse all of them into one generic table or object without preserving their different semantics.

---

# 23. BASELINES

Baseline is not:

```textmean(current values)
```

and nothing more.

A useful baseline may consider:

```textmetric
machine
time of day
day of week
recent history
longer history
data quality
```

Insufficient history must be represented explicitly.

Do not say:

```textnormal
```

when the system has only collected three samples.

---

# 24. ANOMALY SIGNALS

Use multiple signals:

```textabsolute threshold
rate
baseline deviation
persistence
context
```

A single sample should normally have less confidence than a persistent pattern.

---

# 25. PERSISTENCE OF ANOMALIES

Example:

```textone sample > threshold
```

may be noise.

Instead:

```textcondition remains abnormal for N samples
```

becomes more significant.

Use configurable hysteresis where required to avoid flapping.

---

# 26. HYSTERESIS

If an alert opens at:

```textmemory available < 10%
```

it should not immediately recover at:

```text10.01%
```

or it can flap.

Potential model:

```textopen below 10%
recover above 15%
```

The exact values must be based on the metric and documented.

---

# 27. CORRELATION

The correlation engine should connect:

```texttime
resource
process
service
event
history
```

Example:

```textmemory pressure
    ↓
swap activity
    ↓
process RSS growth
    ↓
service ownership
    ↓
journal restart event
    ↓
similar historical incident
```

Correlation windows must be explicit.

Do not correlate events only because they happened "around the same day".

---

# 28. DIAGNOSIS

Diagnosis should produce:

```textcondition
summary
evidence
candidate causes
confidence
limitations
next useful observation
```

A candidate cause is not a confirmed root cause.

Confidence is not certainty.

---

# 29. EVIDENCE OBJECT

Concept:

```python
@dataclass(frozen=True)
class Evidence:
    evidence_type: str
    timestamp: datetime
    subject: str
    observed_value: object
    expected_value: object | None
    source: str
    explanation: str
```

The final implementation may refine types.

The important point is structured evidence rather than free-form text alone.

---

# 30. INCIDENT LIFECYCLE

Initial lifecycle:

```textDETECTED
   ↓
OPEN
   ↓
ACTIVE
   ↓
RECOVERED
```

Possible later states:

```textACKNOWLEDGED
SUPPRESSED
RESOLVED
```

Only add them when the semantics are defined.

---

# 31. INCIDENT DEDUPLICATION

Without deduplication:

```textRAM high
RAM high
RAM high
RAM high
```

creates four alerts.

Instead:

```textone active condition
+
new evidence
=
one incident updated over time
```

Create a new incident only after a real recovery/new occurrence boundary.

---

# 32. FLAPPING

A service or metric can alternate quickly:

```textbad
good
bad
good
```

This should eventually be recognized as flapping.

Flapping is operationally distinct from one isolated failure.

---

# 33. NOTIFICATION ARCHITECTURE

Detection and notification are different.

Detection asks:

```textDid something unusual happen?
```

Notification asks:

```textShould the user be interrupted?
```

Notification policy should consider:

```textseverity
confidence
persistence
deduplication
cooldown
user settings
```

The database incident is the source of truth.

Notification failure must not lose an incident.

---

# 34. NOTIFICATION WARNINGS

Expected product messages include:

### Startup

```textSentinel is starting local monitoring.
Core machine data remains local by default.
Some Linux information may be unavailable because of permissions.
```

### Degraded process access

```textSentinel cannot inspect every process detail on this system.
Monitoring continues with reduced process visibility.
```

### systemd unavailable

```textsystemd service monitoring is unavailable.
Core CPU, memory, disk, network and process monitoring remains active.
```

### Baseline not ready

```textSentinel has not collected enough history to establish a reliable baseline.
Anomaly confidence is temporarily reduced.
```

### Persistent incident

```textSentinel detected sustained memory pressure.

Evidence:
- memory availability has fallen significantly
- swap activity increased
- PID 4312 has shown the largest RSS growth

Review the incident for evidence and limitations.
```

### Recovery

```textRecovered: memory pressure has returned to normal.

Incident: INC-104
Duration: 8m 21s
```

---

# 35. SELF-MONITORING

Sentinel itself must be observable.

Track:

```textdaemon uptime
last successful sample
last failed collector
sampling lag
collector durations
database latency
database size
notification failures
configuration state
```

`sentinel status` should make Sentinel's own health visible.

A monitoring product that silently stops monitoring is not trustworthy.

---

# 36. SAMPLE SCHEDULING

Do not rely forever on:

```python
while True:
    collect()
    sleep(interval)
```

because work duration changes the real sampling interval.

Use a monotonic scheduling model:

```textscheduled deadline
    ↓
collect
    ↓
store/process
    ↓
next deadline
```

Handle overruns explicitly.

For the first implementation, prefer a serial collection cycle before introducing concurrency.

---

# 37. SINGLE INSTANCE

The daemon must prevent accidental duplicate instances.

Do not use only:

```textif lock-file exists:
    process running
```

without handling stale locks.

The mechanism must be safe across:

- crashes
- restarts
- normal shutdown
- user invocation
- service startup

---

# 38. SHUTDOWN AND SIGNALS

Handle:

```textSIGINT
SIGTERM
```

using a minimal signal-handler strategy.

The handler should request shutdown.

Normal control flow should perform:

```textstop scheduling
finish safe current work
commit necessary data
close database
release lock
exit
```

---

# 39. RESOURCE BUDGET

Sentinel must not become the performance problem it is measuring.

Production benchmarking should measure:

```textCPU overhead
memory footprint
database write rate
database growth
log volume
notification overhead
collection latency
```

Do not publish performance percentages until measured.

---

# 40. SELF-THROTTLING

Potential future protections:

```textsampling slowdown
reduced process detail
bounded queues
log rate limiting
notification cooldown
```

The first version should remain deliberately simple but must have explicit resource safeguards.

---

# 41. BACKPRESSURE

If later architecture introduces queues:

```textcollector
    ↓
bounded queue
    ↓
storage writer
```

the queue must be bounded.

Never allow unlimited memory growth because SQLite becomes temporarily slow.

Do not add an async queue merely to look advanced.

---

# 42. NO DATA LOSS BY DEFAULT

A single collection failure should not erase all other observations.

But a partial sample must be labeled partial.

Example:

```textCPU = valid
memory = valid
processes = partial
services = unsupported
```

This is much more trustworthy than pretending the snapshot is complete.

---

# 43. CAPABILITY MODEL

Sentinel should expose capability state.

Example:

```textCPU                    supported
memory                  supported
processes               supported
disk                    supported
network                 supported
systemd                 degraded
journal                 unavailable
desktop notifications   unavailable
GPU                     not implemented
```

Distinguish:

```textsupported
degraded
unavailable
unsupported
not implemented
```

---

# 44. PRIVACY MODEL

Sentinel is local-first.

Do not collect:

- keystrokes
- document contents
- browser history
- packet payloads
- credentials
- unrelated personal content

unless a later product decision explicitly creates a narrowly scoped feature.

Process command lines can contain secrets.

Treat them as sensitive.

---

# 45. COMMAND-LINE PRIVACY

A command may contain:

```text--token=SECRET
--password=...
```

Therefore detailed command-line persistence should be:

- minimized
- redacted where practical
- bounded
- documented
- configurable

Do not claim perfect secret detection.

---

# 46. JOURNAL PRIVACY

Journal logs can contain sensitive information.

Do not dump a full journal into SQLite.

Prefer:

```textbounded event metadata
timestamps
source
unit
priority
short excerpt when justified
```

with explicit retention.

---

# 47. SUBPROCESS SAFETY

Never construct uncontrolled shell commands from user input.

Prefer:

```python
subprocess.run(
    ["systemctl", "list-units", "--type=service"],
    check=True,
    text=True,
    capture_output=True,
)
```

Avoid `shell=True` unless a very strong reason exists and the inputs are controlled.

---

# 48. FILESYSTEM SAFETY

Use `pathlib.Path`.

Never assume a username.

Never assume the home directory.

Use standard user-directory resolution.

Core monitoring should not recursively scan user files.

---

# 49. XDG LOCATIONS

Linux user data should normally use:

```text~/.config/sentinel/
~/.local/share/sentinel/
~/.cache/sentinel/
```

Do not store runtime state in the Git repository.

---

# 50. CONFIGURATION

Suggested configuration:

```text~/.config/sentinel/config.toml
```

Precedence:

```textbuilt-in defaults
    ↓
user config
    ↓
environment
    ↓
explicit CLI arguments
```

Keep configuration surface small.

Configuration is an API and should not grow randomly.

---

# 51. PACKAGE ENTRY POINT

The user should be able to run:

```bash
sentinel
```

rather than:

```bash
python sentinel/main.py
```

Use Python packaging correctly.

Do not make the user's everyday workflow depend on the source repository.

---

# 52. INSTALLATION REALITY

A ZIP cannot universally provide a zero-dependency native application on every Linux machine.

Python versions, shared libraries, systemd, desktop environments and permissions differ.

The distribution strategy should therefore support a clean installation path.

Possible tiers:

```textpipx / Python package
bundled executable later
distribution package later
```

A release archive may contain all necessary application files and an installer, but the product must not falsely promise "unzip and zero setup on every Linux".

---

# 53. SYSTEMD USER SERVICE

Provide a user-level service for systems that support it.

The service should:

- launch the installed Sentinel executable
- restart on failure with a sensible delay
- not require root
- use the actual installed executable path
- avoid hard-coded user directories

Do not assume systemd user services exist on every Linux.

`sentinel doctor` should report capability.

---

# 54. CLI DESIGN

Core commands:

```textsentinel
sentinel status
sentinel watch
sentinel top
sentinel history
sentinel diff
sentinel process <pid>
sentinel services
sentinel incidents
sentinel diagnose <incident-id>
sentinel report
sentinel doctor
sentinel config
sentinel version
```

Possible future:

```textsentinel backup
sentinel export
sentinel repair
```

---

# 55. CLI OUTPUT MODES

Important commands should support:

```texthuman-readable
JSON
quiet
verbose
```

Do not mix ANSI escape sequences into JSON.

Exit codes must be stable and documented.

---

# 56. DOCTOR COMMAND

`sentinel doctor` is a first-class support tool.

It should check:

```textPython runtime
OS
architecture
/proc
/sys
process visibility
systemd
journal
database
disk space
configuration
notification backend
daemon
```

Example:

```textSentinel Doctor

✓ Python runtime
✓ Linux
✓ /proc
✓ /proc/meminfo
✓ process inspection
✓ SQLite writable
✓ configuration valid
! systemd user service unavailable
! desktop notification unavailable

Result:
Core monitoring available with degraded optional capabilities.
```

---

# 57. JSON INTERFACE

Example:

```json
{
  "schema_version": "1",
  "sentinel_version": "1.0.0",
  "timestamp": "2026-09-11T12:00:00Z",
  "system": {},
  "health": {},
  "capabilities": {},
  "active_incidents": []
}
```

Schema version is separate from application version.

---

# 58. AI AGENT INTERFACE

The AI agent should receive a compact evidence package, not an unlimited database dump.

Concept:

```json
{
  "schema_version": "1",
  "current_state": {},
  "recent_changes": [],
  "active_incidents": [],
  "historical_context": {},
  "limitations": []
}
```

Questions the interface should eventually answer:

```textWhat is happening?
What changed?
When did it start?
What is affected?
Why is it unusual?
Which process/service is involved?
Has it happened before?
What evidence supports the conclusion?
What evidence is missing?
```

---

# 59. TOKEN-EFFICIENT AI CONTEXT

Do not send:

```text10,000 raw samples
```

if the question concerns one incident.

Build a relevant context:

```textcurrent state
+
recent deltas
+
baseline summary
+
relevant evidence
+
historical similarity
+
limitations
```

This provides more useful AI reasoning while reducing context cost.

The long-term data remains on the machine.

---

# 60. AI REMEDIATION SAFETY

Initial AI interface is read-only.

No direct:

```textkill
restart
delete
modify
```

from an LLM.

If remediation is eventually implemented:

```textobserve
→ propose
→ request explicit approval
→ execute a predefined safe action
→ verify
→ record audit trail
```

No arbitrary LLM-generated shell execution.

---

# 61. AUDIT TRAIL

If any mutating action is ever added, record:

```textrequester
action
reason
approval
timestamp
execution result
verification result
```

Auditability is mandatory before automated remediation.

---

# 62. TESTING STRATEGY

## Unit tests

Test:

- parsers
- unit conversion
- models
- CPU formulas
- rate formulas
- thresholds
- baseline calculations
- incident transitions
- severity
- deduplication

---

## Fixtures

Use deterministic Linux fixtures.

Examples:

```text/proc/meminfo
/proc/stat
/proc/net/dev
/proc/<pid>/status
/proc/<pid>/cmdline
/etc/os-release
systemctl output
journal output
```

Do not make all tests depend on the developer's current machine.

---

## Integration tests

On Linux, test:

- real `/proc`
- real process enumeration
- real filesystem capacity
- real network counters
- systemd capability detection
- SQLite lifecycle

Skip capabilities only when genuinely unavailable.

---

## Safety

Tests must not:

- kill arbitrary processes
- restart arbitrary services
- modify system files
- delete user files
- require root

---

# 63. EDGE CASE MATRIX

Mandatory cases eventually include:

```textempty input
malformed input
missing field
unknown field
unknown unit
permission denied
process disappears
service disappears
interface disappears
counter reset
counter decreases
reboot
suspend/resume
clock adjustment
zero elapsed time
negative elapsed time
no network interface
no systemd
no desktop
very large command line
non-UTF8 process metadata
duplicate service data
partial snapshot
database full
database locked
configuration invalid
duplicate daemon
stale lock
```

---

# 64. ERROR-HANDLING RULES

Never write:

```python
except Exception:
    pass
```

Differentiate:

```textexpected OS race
permission issue
unsupported capability
malformed source data
storage failure
configuration failure
programming bug
```

Expected failures can be structured.

Unexpected programming errors must remain visible during tests/development.

---

# 65. OBSERVATION QUALITY

A snapshot should eventually have quality information.

Example:

```textquality = degraded
reasons:
- process collector lost 5 processes during races
- service collector unavailable
```

This prevents downstream analysis from assuming complete data.

---

# 66. DATABASE FAILURE MODE

If history cannot be written:

```textcurrent observation may still exist
history becomes degraded
incident persistence may be unavailable
```

The product should clearly tell the user.

Do not silently operate for days without persistence and pretend history exists.

---

# 67. SELF-HEALTH MODEL

Sentinel itself should eventually expose:

```textdaemon_running
last_sample
last_successful_sample
sampling_lag
collector_failures
database_status
database_size
notification_status
```

This is required for trust.

---

# 68. REBOOT DETECTION

Uptime resets after reboot.

Do not calculate:

```textnegative uptime change = anomaly
```

Detect the new boot/session epoch.

Historical analysis must understand reboot boundaries.

---

# 69. SUSPEND/RESUME

A laptop can sleep for hours.

Do not interpret the gap as normal 5-second sampling.

Record gaps explicitly.

A time series should be aware of missing intervals.

---

# 70. SERVICE FLAPPING

If:

```textfailed
active
failed
active
```

happens repeatedly, it is an operational condition.

Store transitions.

Do not use only current `active_state`.

---

# 71. RECOVERY

Recovery should be modeled explicitly.

Example:

```text14:01 detected
14:07 condition reduced
14:09 recovered
```

Persist:

```textstart
recovery
duration
```

Recovery notification is optional but useful.

---

# 72. SILENCE AND NOTIFICATION POLICY

An anomaly does not necessarily deserve a desktop popup.

Examples:

```text2-second CPU spike
normal build workload
known backup window
expected boot activity
```

Notification policies are separate from anomaly detection.

---

# 73. MACHINE-SPECIFIC HISTORY

The system should recognize that each machine is different.

A baseline learned on one machine must not silently migrate to another.

Machine identity should eventually be scoped to an installation/machine instance.

Be careful with personally identifying identifiers.

Do not upload them.

---

# 74. NO NETWORK BY DEFAULT

Core Sentinel should operate fully offline.

Future network features must be:

- explicit
- documented
- visible in configuration
- disabled by default unless a deliberate product decision changes this

No hidden telemetry.

---

# 75. RESOURCE BOUNDS

Every long-running subsystem needs bounds.

Examples:

```textdatabase retention
log size
event size
journal excerpt size
queue depth
notification frequency
AI context size
history query size
```

No unbounded state.

---

# 76. QUERY BOUNDS

A command like:

```bash
sentinel history
```

must not load the entire database into Python memory.

Use:

```textpagination
aggregation
bounded windows
```

and sensible defaults.

---

# 77. LARGE PROCESS SETS

Servers can have thousands of processes.

Avoid:

```textcollect every field
→ build massive object graph
→ serialize everything
```

without need.

Define:

```textminimum observation set
optional detailed metadata
```

and benchmark.

---

# 78. PROCESS COMMAND REDACTION

A practical redaction layer may recognize obvious patterns:

```text--password
--token
--secret
API keys
authorization headers
```

This can reduce exposure but is not perfect.

Document that redaction is heuristic.

---

# 79. JOURNAL CORRELATION

Do not duplicate large journal content.

Prefer references:

```texttimestamp
unit
priority
identifier
short bounded message/excerpt
```

Correlation can query adjacent events when needed.

---

# 80. FUTURE GPU

GPU support is optional.

Potential backends include:

```textNVIDIA
AMD
Intel
```

Capability detection must decide what is available.

NVIDIA-specific tooling cannot be a core dependency.

---

# 81. FUTURE CONTAINERS

Container support should be an explicit extension.

Potential sources:

```textcgroups
Docker
Podman
containerd
Kubernetes
```

Do not put container-specific logic into base Linux process collection unless necessary.

---

# 82. FUTURE PLUGINS

Possible plugins:

```textPostgreSQL
Redis
Nginx
Docker
GPU
application-specific metrics
```

A plugin system must define:

```textpermissions
configuration
failure isolation
schema/versioning
resource limits
trust model
```

Do not allow arbitrary untrusted plugins to execute with elevated privileges.

---

# 83. FUTURE ADAPTIVE SAMPLING

A mature implementation may eventually sample more aggressively during active incidents.

Normal:

```text5–15 seconds
```

Incident:

```texttemporarily faster
```

But implement fixed-rate sampling first.

---

# 84. AGGREGATION

A mature retention system may keep:

```textraw high-resolution:
short duration

minute aggregates:
longer duration

hour aggregates:
long duration
```

Incidents should remain independently preserved.

Do not throw away incident evidence merely because raw samples expire.

---

# 85. STORAGE EXPORT

Provide eventual:

```bash
sentinel export
sentinel backup
```

Exports should be explicit and safe.

Do not export secrets by default.

---

# 86. UNINSTALL

The uninstall workflow must distinguish:

```textapplication
configuration
historical data
systemd service
logs
```

History must not be deleted silently.

Potential prompt:

```textSentinel data contains historical observations and incidents.
Deleting it is irreversible without a backup.

Delete historical data? [y/N]
```

---

# 87. DOCUMENTATION REQUIREMENTS

Repository must include:

```textREADME.md
docs/architecture.md
docs/installation.md
docs/configuration.md
docs/privacy.md
docs/security.md
docs/troubleshooting.md
docs/collectors.md
docs/storage.md
docs/analysis.md
docs/incidents.md
docs/ai-interface.md
docs/release.md
```

README should remain approachable.

Deep implementation details belong in `docs/`.

---

# 88. MULTI-AGENT DEVELOPMENT MODEL

The project should be deliberately divided among specialized agents.

The key rule:

> Agents own responsibilities, not random files.

Recommended agents:

## Agent 0 — Architect / integrator

Responsibilities:

- architecture contracts
- integration
- dependency direction
- interface stability
- final review
- phase gates

It does not rewrite everything unnecessarily.

---

## Agent 1 — Linux platform specialist

Responsibilities:

- `/proc`
- `/sys`
- OS detection
- systemd detection
- journal interface
- Linux capability detection
- permission handling

Primary references:

- TLPI
- OSTEP

---

## Agent 2 — Collector specialist

Responsibilities:

- memory
- CPU
- system
- process
- disk
- network
- services
- collector status
- parser fixtures

Primary references:

- TLPI
- Python Cookbook

---

## Agent 3 — Domain model specialist

Responsibilities:

- observation models
- snapshot
- evidence
- anomaly
- incident types
- status enums
- units
- invariants

Primary references:

- Python Cookbook
- SRE

---

## Agent 4 — Storage specialist

Responsibilities:

- SQLite
- schema
- migrations
- repositories
- transactions
- indexes
- retention
- backup

Primary reference:

- DDIA

---

## Agent 5 — Time-series / analysis specialist

Responsibilities:

- rates
- deltas
- counter resets
- reboot boundaries
- baseline
- anomaly detection

Primary references:

- OSTEP
- SRE
- DDIA

---

## Agent 6 — Correlation / diagnosis specialist

Responsibilities:

- evidence
- correlation
- historical similarity
- diagnosis
- confidence
- limitations

Primary references:

- SRE
- SRE Workbook

---

## Agent 7 — Daemon/runtime specialist

Responsibilities:

- scheduling
- signals
- lifecycle
- locks
- systemd user service
- self-monitoring
- resource bounds

Primary references:

- TLPI
- Python Cookbook
- SRE

---

## Agent 8 — CLI / product UX specialist

Responsibilities:

- command routing
- human output
- JSON
- exit codes
- doctor
- reports
- history views
- notifications

Primary references:

- SRE
- product engineering principles

---

## Agent 9 — Security/privacy specialist

Responsibilities:

- subprocess safety
- permissions
- sensitive metadata
- command-line redaction
- journal privacy
- storage permissions
- network isolation

Primary references:

- TLPI
- secure Python practices
- product requirements

---

## Agent 10 — Test / release specialist

Responsibilities:

- unit tests
- fixtures
- integration tests
- smoke tests
- packaging
- installation
- upgrade
- uninstall
- release verification

---

# 89. AGENT OWNERSHIP MATRIX

```text
Domain                  Primary agent    Review agents
------------------------------------------------------
Linux platform          A1               A2, A9
Collectors              A2               A1, A10
Models                  A3               A0, A10
Storage                 A4               A0, A9, A10
Analysis                A5               A0, A6
Diagnosis               A6               A0, A5, A9
Daemon                  A7               A1, A9, A10
CLI                     A8               A0, A10
Security                A9               A0, A1, A4
Release/tests            A10              all owners
Integration              A0               all owners
```

The review agent should be different from the original implementer for critical modules.

---

# 90. AGENT CONTEXT STRATEGY

Do not send the entire repository to every agent every time.

Use hierarchical context.

Permanent:

```textarchitecture
contracts
security rules
phase state
schema contracts
```

Task-specific:

```textrelevant files
relevant interfaces
tests
acceptance criteria
```

Review-specific:

```textdiff
relevant failure modes
benchmark results
test results
```

This saves tokens and reduces context noise.

---

# 91. AGENT TASK TEMPLATE

Every task should contain:

```textROLE
You are the specialist responsible for <domain>.

GOAL
Implement <specific capability>.

READ FIRST
<architecture document>
<relevant contracts>
<relevant source files>

ALLOWED AREA
<directories/modules>

MUST PRESERVE
<interfaces and invariants>

FAILURE SEMANTICS
<expected degraded/unsupported cases>

SECURITY
<security constraints>

TESTS
<required tests>

ACCEPTANCE
<precise conditions>

DO NOT
<out-of-scope items>

DELIVERABLE
<code + tests + docs>
```

This is much more reliable than asking an LLM to "make it production ready."

---

# 92. AGENT COMMUNICATION CONTRACT

Agents should communicate:

```textwhat changed
why
files touched
interfaces changed
tests run
test results
known limitations
assumptions
follow-up risks
```

Do not communicate only:

```text"done"
```

---

# 93. MERGE GATES

Before integrating another agent:

```texttests pass
type/syntax checks pass
architecture boundaries pass
security review pass where applicable
no unintended dependency added
```

Integration agent reviews the diff.

---

# 94. CONTRACT-FIRST DEVELOPMENT

For major modules:

```textdefine model
define interface
define failure states
write tests
implement
integrate
```

Do not build a giant implementation first and discover semantics later.

---

# 95. REVIEW ORDER

A review should inspect:

1. correctness
2. Linux semantics
3. race conditions
4. error semantics
5. security
6. data durability
7. performance
8. API stability
9. testing
10. documentation

Not only formatting.

---

# 96. DO NOT OVER-ABSTRACT

Avoid unnecessary:

```textfactories
registries
interfaces
abstract classes
dependency containers
event buses
```

unless they solve a real problem.

Good engineering is not the number of abstractions.

---

# 97. NO PATCH CULTURE

If a change reveals a broken architectural assumption:

```textupdate contract
update architecture
update tests
then update implementation
```

Do not pile five patches onto an incorrect design.

---

# 98. NO AI SLOP

Avoid:

- giant functions
- unused abstractions
- generic "Manager" classes
- repeated helper functions
- copy-pasted parsing
- hidden global state
- magic constants
- broad `except Exception`
- comments that merely restate code
- invented metrics
- fake confidence scores
- fake benchmarks
- meaningless "production-ready" claims

---

# 99. PYTHON STYLE REQUIREMENTS

Prefer:

```texttype hints
dataclasses
Path
context managers
explicit return types
small deterministic functions
clear names
standard library
```

Use mutable state only where needed.

Use frozen domain observations where practical.

---

# 100. DEPENDENCY POLICY

Standard library first.

Third-party dependency requires a written justification:

```textcapability
why stdlib is insufficient
performance impact
security/maintenance impact
installation impact
```

Do not add a dependency because it saves twenty lines.

---

# 101. WHY NOT PSUTIL BY DEFAULT

`psutil` is a legitimate library and may eventually be used.

But Sentinel's foundational phase should understand Linux's native interfaces.

Direct Linux collection provides:

- control
- Linux semantics
- lower abstraction ambiguity
- deeper systems learning
- fewer runtime dependencies

If benchmarking later proves that a dependency gives significant product value, introduce it deliberately.

---

# 102. WHY NOT AN LLM FIRST

An LLM can explain data.

It cannot retroactively create trustworthy machine history that Sentinel never collected.

The product asset is:

```textreal machine
+
continuous observation
+
historical context
+
evidence
```

AI is an interface and reasoning layer, not the foundation.

---

# 103. SECURITY MODEL

Sentinel should run without root for normal monitoring.

Requirements:

- least privilege
- read-only by default
- no uncontrolled shell execution
- no arbitrary command evaluation
- no hidden networking
- no secret collection
- restrictive runtime file permissions
- safe database path
- bounded logging
- safe config handling

---

# 104. ROOT POLICY

Do not require:

```bash
sudo sentinel
```

as normal behavior.

If a specific optional capability requires privileges:

```textdetect
document
degrade gracefully
```

Do not make the whole product privileged.

---

# 105. PLATFORM ABSTRACTION

Sentinel is Linux-first.

It should have an OS/capability boundary.

Future:

```textplatform/
    linux/
    windows/
    macos/
```

But do not claim support until implemented and tested.

---

# 106. DISTRIBUTION

A production Linux application should offer at least:

```textPython package installation
```

and eventually:

```textstandalone/bundled distribution
distribution-native packages
```

A release archive can include:

```textsource
installer
systemd unit
documentation
license
checksums
```

Do not confuse "ZIP available" with "runs universally without installation".

---

# 107. INSTALL EXPERIENCE

Target:

```textinstall Sentinel
        ↓
sentinel doctor
        ↓
sentinel
        ↓
background monitoring
```

The user should not manually run a Python script every day.

---

# 108. UPDATE EXPERIENCE

Upgrades must preserve:

```textdatabase
configuration
incident history
```

unless a deliberate migration is required.

Database migrations must run safely.

Never silently delete history to fix a schema mismatch.

---

# 109. VERSIONING

Separate:

```textapplication version
database schema version
JSON schema version
```

Example:

```json
{
  "sentinel_version": "1.4.0",
  "schema_version": "3",
  "api_schema_version": "1"
}
```

---

# 110. RELEASE SMOKE TEST

A release should verify:

```textinstall
start
collect
persist
restart
upgrade
doctor
JSON output
notification behavior where available
uninstall
data preservation/deletion semantics
```

---

# 111. PHASE 1 ACCEPTANCE CHECKLIST

Phase 1 is complete only if:

```text[ ] Linux detected
[ ] capabilities detected
[ ] memory collector works
[ ] CPU counters work
[ ] system collector works
[ ] process collector works
[ ] disk collector works
[ ] network collector works
[ ] systemd collector optional
[ ] models exist
[ ] SystemSnapshot exists
[ ] collection status exists
[ ] permission failures represented
[ ] disappearing processes handled
[ ] malformed input handled
[ ] units explicit
[ ] UTC timestamps
[ ] no username hard-coding
[ ] no root requirement
[ ] standard library runtime
[ ] unit tests
[ ] fixtures
[ ] integration tests
[ ] documentation
```

---

# 112. PHASE 2 ACCEPTANCE CHECKLIST

```text[ ] SQLite schema
[ ] migrations
[ ] repositories
[ ] transactions
[ ] indexes
[ ] retention
[ ] database health
[ ] snapshot persistence
[ ] process persistence
[ ] events
[ ] incident persistence foundation
[ ] tests
```

---

# 113. PHASE 3 ACCEPTANCE CHECKLIST

```text[ ] daemon
[ ] scheduler
[ ] monotonic timing
[ ] signals
[ ] single instance
[ ] graceful shutdown
[ ] systemd user service
[ ] self-health
[ ] restart safety
```

---

# 114. PHASE 4 ACCEPTANCE CHECKLIST

```text[ ] CPU utilization
[ ] network rate
[ ] disk rate where available
[ ] process CPU
[ ] memory growth
[ ] counter reset
[ ] reboot boundary
[ ] gaps
```

---

# 115. PHASE 7–11 ANALYSIS ACCEPTANCE

```text[ ] machine-specific baseline
[ ] sufficient-history state
[ ] thresholds
[ ] persistence
[ ] hysteresis
[ ] anomaly
[ ] correlation
[ ] evidence
[ ] diagnosis
[ ] confidence
[ ] limitations
[ ] incidents
[ ] recovery
[ ] deduplication
[ ] flapping
```

---

# 116. PRODUCT QUALITY BAR

A production product means:

```textcorrectness
+
safe failure
+
data durability
+
security
+
supportability
+
upgrade path
+
clear UX
+
tests
+
documentation
+
measured performance
```

Not:

```textmany files
lots of AI
fancy UI
```

---

# 117. FINAL USER EXPERIENCE

A user should be able to install Sentinel and eventually reach:

```text$ sentinel

Sentinel
Machine: enzolinux
Kernel: 6.x
Uptime: 2d 4h

Health: normal

CPU       14%
Memory    41%
Disk      62%

Active incidents: 0
Last sample: 4s ago
Monitoring: active
History: 11d
```

Then:

```text$ sentinel incidents

No active incidents.

Recent:
INC-104 recovered 2h ago
memory pressure
duration 8m 21s
```

Then:

```text$ sentinel diagnose INC-104
```

returns:

```textCondition:
sustained memory pressure

Evidence:
- available memory fell from 7.8 GiB to 1.1 GiB
- swap activity increased
- PID 4312 RSS grew from 820 MiB to 4.2 GiB
- process growth was outside its baseline
- similar incident occurred three times previously

Likely cause:
sustained process memory growth associated with PID 4312

Confidence:
high

Limitations:
journal access was partial
```

This is the product value.

---

# 118. THE AGENT INTERFACE VISION

Eventually an AI coding/operations agent can query:

```textsentinel report --json
```

and receive a bounded machine-context package.

The agent can then reason:

```textThe machine is under sustained memory pressure.
PID 4312 is the largest growth contributor.
The process is associated with service X.
The condition resembles three prior incidents.
Journal access is partial, so root cause is not fully confirmed.
```

The AI is useful because Sentinel already did the hard local observation/history work.

---

# 119. THE PRODUCT'S CORE LOOP

The entire system can be reduced to:

```textObserve
    ↓
Remember
    ↓
Compare
    ↓
Correlate
    ↓
Explain
```

Everything else exists to make that loop trustworthy.

---

# 120. MASTER IMPLEMENTATION ORDER

The actual coding process should follow:

```textPHASE 0
contracts + package

↓
PHASE 1
Linux observation foundation

↓
PHASE 2
persistent history

↓
PHASE 3
daemon

↓
PHASE 4
rates

↓
PHASE 5
process intelligence

↓
PHASE 6
services/events

↓
PHASE 7
baselines

↓
PHASE 8
anomalies

↓
PHASE 9
correlation

↓
PHASE 10
diagnosis

↓
PHASE 11
incidents

↓
PHASE 12
CLI + notifications + doctor

↓
PHASE 13
AI interface

↓
PHASE 14
distribution/release
```

---

# 121. FIRST CODEX MASTER TASK

Paste this task to the primary implementation agent after it has access to the repository and this specification:

```text
You are the primary Sentinel implementation engineer.

Read the complete Sentinel Master Production Build Specification before editing.

Your job is to implement the system as a coherent production-grade Linux product, not as a sequence of tutorial patches.

First:
1. inspect the repository
2. inspect the existing source tree
3. inspect pyproject.toml
4. inspect README
5. compare actual code against the specification
6. identify missing architectural contracts
7. preserve valid work
8. make a phase plan

Then implement ONLY the currently authorized phase.

For Phase 1, implement:

- platform detection
- capability detection
- collection result/status semantics
- memory collector
- CPU collector
- system collector
- process collector
- disk collector
- network collector
- optional systemd service collector
- typed models
- SystemSnapshot
- collection orchestration
- unit tests
- parser fixtures
- Linux integration tests where appropriate
- Phase 1 documentation

Important:
- do not implement SQLite yet
- do not implement baselines yet
- do not implement anomaly detection yet
- do not implement diagnosis yet
- do not implement incidents yet
- do not implement LLM integration yet
- do not build a web dashboard
- do not add unnecessary third-party dependencies
- do not use hard-coded usernames, paths, interface names or service names
- do not require root
- do not hide errors with broad exception handling
- do not treat unavailable as zero
- do not treat PID alone as process identity
- do not calculate rates from one sample
- do not treat low MemFree as automatically low memory
- use UTC for persisted timestamps
- use monotonic timing for elapsed duration
- handle processes disappearing during collection
- handle systemd being absent
- report partial snapshot quality

Before finishing:
- run all tests
- run syntax/type/lint checks available in the project
- inspect dependency graph
- inspect for architecture violations
- inspect for hard-coded environment assumptions
- inspect for security problems
- inspect for accidental network access
- inspect for unnecessary dependencies
- update documentation
- produce a concise engineering report listing files changed, tests run, assumptions, and known limitations

Do not call the phase complete because the code merely runs once.
```

---

# 122. SECOND REVIEW TASK

After the primary agent finishes, run a separate review agent:

```text
You are the Sentinel architecture and Linux-semantics reviewer.

Do not blindly trust the implementation.

Read:
- Sentinel Master Production Build Specification
- all Phase 1 code
- all tests
- documentation
- pyproject.toml

Audit:
1. architecture boundaries
2. /proc and Linux semantics
3. process races
4. PID identity
5. permission handling
6. systemd optionality
7. timestamp semantics
8. units
9. unavailable vs zero
10. malformed source data
11. security
12. subprocess usage
13. resource overhead
14. dependency surface
15. test coverage
16. packaging assumptions

Classify findings:
CRITICAL
HIGH
MEDIUM
LOW

Do not rewrite code unless necessary for the review.

Return:
- findings
- evidence
- required fixes
- optional improvements
- phase gate recommendation
```

---

# 123. THIRD REVIEW TASK — SECURITY

```text
Audit Sentinel Phase 1 as a security/privacy engineer.

Focus on:
- subprocess invocation
- command construction
- shell injection
- permissions
- local file permissions
- process command-line exposure
- secret exposure
- path handling
- unexpected networking
- logging
- sensitive data persistence
- privilege assumptions

Do not optimize for style.

Find concrete security risks and provide severity and remediation.
```

---

# 124. FOURTH REVIEW TASK — TESTING

```text
Audit Sentinel Phase 1 as the test/reliability engineer.

Find missing tests for:
- parser corruption
- missing fields
- unknown fields
- unknown units
- permission denied
- process disappearance
- service absence
- systemd absence
- malformed command output
- counter semantics
- timestamps
- partial results
- capability reporting

Add deterministic fixtures where useful.

Do not make tests destructive or root-dependent.
```

---

# 125. INTEGRATOR FINAL GATE

Only the integrator decides:

```textPASS
```

when:

```textimplementation passes
+
review passes
+
security passes
+
tests pass
+
documentation matches code
```

If reviewers disagree, the integrator resolves the contract before implementation continues.

---

# 126. IMPORTANT: ONE-DAY LLM TASK DOES NOT MEAN ONE GIANT PROMPT

The entire Sentinel can be developed quickly with LLM assistance, but quality comes from controlled decomposition.

Use:

```textarchitecture context
→ specialist agent
→ tests
→ reviewer
→ integrator
```

rather than:

```textone giant prompt
→ 20,000 lines
→ hope
```

The second approach creates hidden assumptions and debugging debt.

---

# 127. THE ACTUAL STRATEGY

The project is large.

That is fine.

Do not rush it.

The correct strategy is:

```textfreeze architecture
↓
build foundation
↓
measure
↓
persist
↓
derive
↓
reason
↓
explain
↓
package
```

The LLM is the workforce multiplier.

The architecture is the control system.

The books are the conceptual foundation.

The local machine is the source of truth.

The historical database is the memory.

The analysis layer is the deterministic reasoning engine.

The AI interface is the final intelligence bridge.

---

# 128. FINAL PRODUCT PRINCIPLE

Sentinel should never say:

```text"Trust me, something is wrong."
```

It should be able to say:

```text"Here is what I observed.
Here is what changed.
Here is what is unusual.
Here is what correlates.
Here is why I think it matters.
Here is my confidence.
Here is what I cannot observe.
Here is what happened previously."
```

That is the standard.

