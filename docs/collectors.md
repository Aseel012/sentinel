# Collectors

Memory reads `/proc/meminfo` and exposes bytes. CPU and network collectors expose cumulative counters only; a single sample cannot establish utilisation or throughput. Disk observations are filesystem capacity, not disk I/O.

Process collection is race tolerant. Sentinel identifies a process lifetime with `pid:start_time_ticks`, captures a bounded redacted command line, and records inaccessible or disappearing processes as partial collection. Systemd service inspection is optional and uses a fixed, shell-free command with a timeout.

Service and journal commands share a shell-free subprocess reader that enforces a combined stdout/stderr byte budget while draining pipes and a wall-time deadline. An installed executable is only a capability hint: `doctor` may report support while an actual collection reports an unavailable manager or denied access.

Journal collection is separate from snapshots. The first poll reads a bounded oldest-first window of the retained journal visible to the account. Subsequent polls seek inclusively to the saved cursor, verify that exact source identity, skip that already-consumed record, and consume the next bounded window. This avoids tail-based paging that skips intermediate records during bursts. The default batch accepts at most 200 new events. Boot context is retained per event; cursors, rather than message/timestamp pairs, distinguish identical-looking events.

A burst produces partial quality and the last accepted cursor, so the next poll continues from that point. A valid cursor with no new entries produces a successful empty batch. Missing executables, permission failures, invalid data, timeouts, and cursor invalidation remain explicit. If rotation removes the saved identity, ingestion reports uncertainty and preserves the checkpoint for explicit operator recovery. It does not silently reset and claim continuous history.

Journal fields and messages are bounded; truncation and redaction are visible in event warnings and aggregate collection quality. These warnings survive storage. Invalid records and output-budget failures never advance beyond unaccepted data. Raw journal output is not persisted. Common credential-form redaction is a mitigation with the limitations described in [privacy](privacy.md).
