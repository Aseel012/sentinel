"""Foreground, monotonic scheduling for repeated snapshot persistence."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class SnapshotRecorder(Protocol):
    """The narrow application boundary required by the sampling loop."""

    def record(self) -> object:
        """Collect and atomically persist one snapshot."""


@dataclass(frozen=True, slots=True)
class SamplingConfig:
    """Sampling configuration; the default is intentionally conservative."""

    interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        value = self.interval_seconds
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("interval_seconds must be a real number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("interval_seconds must be finite and greater than zero")


@dataclass(frozen=True, slots=True)
class SamplingRun:
    """A completed foreground run; individual samples are persisted by the recorder."""

    samples_completed: int
    stopped: bool


class Sampler:
    """Run one synchronous sample at a time on a monotonic deadline schedule.

    The first sample starts immediately.  Later deadlines advance from the original
    schedule, rather than from each completed sample, preventing ordinary work time
    from accumulating drift.  If work overruns one or more intervals, missed
    deadlines are skipped; Sentinel neither overlaps samples nor queues catch-up work.
    Persistence and unexpected exceptions intentionally propagate to the caller.
    """

    def __init__(
        self,
        recorder: SnapshotRecorder,
        config: SamplingConfig | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._recorder = recorder
        self._config = config or SamplingConfig()
        self._monotonic = monotonic
        self._stop_event = threading.Event()
        self._wait = wait or self._stop_event.wait
        self._lifecycle_lock = threading.Lock()
        self._running = False

    def request_stop(self) -> None:
        """Request a graceful stop; an active synchronous record is allowed to finish."""
        self._stop_event.set()

    def reset_stop_request(self) -> None:
        """Clear a prior stop request when the sampler is not currently running."""
        with self._lifecycle_lock:
            if self._running:
                raise RuntimeError("cannot reset a sampler while it is running")
            self._stop_event.clear()

    def run(self, *, max_samples: int | None = None) -> SamplingRun:
        """Run in the foreground until stopped, or through an optional testable bound."""
        if max_samples is not None and (isinstance(max_samples, bool) or not isinstance(max_samples, int)):
            raise TypeError("max_samples must be an integer or None")
        if max_samples is not None and max_samples < 0:
            raise ValueError("max_samples cannot be negative")
        with self._lifecycle_lock:
            if self._running:
                raise RuntimeError("sampler is already running")
            self._running = True
        try:
            completed = 0
            next_deadline = self._monotonic()
            while not self._stop_event.is_set():
                if max_samples is not None and completed >= max_samples:
                    break
                self._recorder.record()
                completed += 1
                if self._stop_event.is_set() or (max_samples is not None and completed >= max_samples):
                    break
                next_deadline += self._config.interval_seconds
                now = self._monotonic()
                while next_deadline <= now:
                    next_deadline += self._config.interval_seconds
                if self._wait(next_deadline - now):
                    break
            return SamplingRun(completed, self._stop_event.is_set())
        finally:
            with self._lifecycle_lock:
                self._running = False
