# Privacy and security

Sentinel performs local, read-only observation with no telemetry or network access. It does not collect keystrokes, document contents, browser history, packet payloads, or credentials.

Process command lines can contain secrets. Sentinel bounds them to 1024 characters and redacts common `token`, `password`, `secret`, API-key, and authorization assignments. This is best-effort and must not be treated as perfect secret detection. The Phase 1 release stores no history.
