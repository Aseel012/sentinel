# Collectors

Memory reads `/proc/meminfo` and exposes bytes. CPU and network collectors expose cumulative counters only; a single sample cannot establish utilisation or throughput. Disk observations are filesystem capacity, not disk I/O.

Process collection is race tolerant. Sentinel identifies a process lifetime with `pid:start_time_ticks`, captures a bounded redacted command line, and records inaccessible or disappearing processes as partial collection. Systemd service inspection is optional and uses a fixed, shell-free command with a timeout.
