# Privacy and security

Sentinel observes the local system and makes no telemetry or network requests. Explicit persistence services retain history in a local SQLite database. Ordinary `status` remains read-only. There are no dedicated collectors for keystrokes, document contents, browser history, or packet payloads.

Process command lines and journal messages can contain credentials, personal information, and application data. Command text is capped at 1024 characters. Journal fields and messages have explicit bounds; truncation and other transformations are represented in event warnings and collection quality. A shared regex helper mitigates common inline credential forms. It cannot guarantee that secrets will not be collected or persisted; quoted, multiline, encoded, and unfamiliar secret forms can escape it.

Journal collection is explicitly invoked through `JournalEventService`; it reads only what the current account can access and never elevates permissions. It stores selected event fields, per-event warnings, and the latest collection result. It does not retain raw journal JSON or subprocess diagnostic text. Existing database permissions restrict database files to the owner, but there is no encryption at rest.

Journal writes retain at most 10,000 event rows by default (configurable through the application service). The existing retention module also supports an explicit UTC cutoff. Deletion preserves the checkpoint, so retention does not replay old journal entries. SQLite may reuse freed pages without shrinking the database file; this is neither secure erasure nor an exact disk-byte quota. Collection-quality metadata describes the latest poll, not an unlimited audit log.
