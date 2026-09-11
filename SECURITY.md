# Security policy

Sentinel Phase 1 is an unprivileged, read-only Linux observer. Please report vulnerabilities privately to the project maintainers rather than opening a public issue with exploit details.

Security boundaries in this release: no network access or telemetry; no shell execution; fixed `systemctl` arguments with a timeout; bounded/redacted process command metadata; and structured collection failures instead of fabricated values. Sentinel does not yet create a database, daemon lock, configuration file, or runtime state.
