# Sentinel Phase 1 architecture

Sentinel is deliberately local-first and read-only. Linux access is isolated in collectors; immutable models perform no I/O; the application service composes collector results; and the CLI formats application output only.

Every collector returns a `CollectionResult`. `success`, `partial`, `permission_denied`, `unsupported`, `transient_failure`, and `invalid_data` are distinct states. An unavailable process or service list is never represented as a successful empty list.

Phase 2 adds a versioned SQLite memory foundation. Schema migrations are explicit and transactional; queryable snapshot children use foreign keys with cascading deletion for future retention. Timestamps are UTC ISO-8601 text, process history uses PID plus `start_time_ticks`, and raw CPU/network counters are stored without premature rate calculations. Observations are relational columns rather than a monolithic JSON blob. Repositories, retention policy, analysis, diagnosis, incidents, and networking remain outside this scope.

Phase 3 adds a reusable foreground sampler. It starts one collection/persistence operation immediately, then advances monotonic deadlines by the configured interval (60 seconds by default). Work duration does not accumulate normal drift. An overrun skips missed deadlines instead of creating queued or overlapping samples. `request_stop()` prevents the next iteration and interrupts the normal wait; an in-progress synchronous sample is allowed to finish. Collector quality remains part of each stored snapshot. Persistence and unexpected errors propagate to the caller, while the sampler performs no health analysis or notification.
