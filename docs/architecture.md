# Sentinel Phase 1 architecture

Sentinel is deliberately local-first and read-only. Linux access is isolated in collectors; immutable models perform no I/O; the application service composes collector results; and the CLI formats application output only.

Every collector returns a `CollectionResult`. `success`, `partial`, `permission_denied`, `unsupported`, `transient_failure`, and `invalid_data` are distinct states. An unavailable process or service list is never represented as a successful empty list.

This release implements the Phase 1 observation foundation. It deliberately does not persist observations, calculate rates, create baselines, diagnose conditions, manage incidents, or contact any network service.
