"""Shell-free subprocess reads with a combined byte budget and deadline."""

from __future__ import annotations

import os
import selectors
import subprocess
import time


class OutputLimitExceeded(ValueError):
    """The child exceeded its combined stdout/stderr budget."""


def run_bounded(command: list[str], *, timeout: float, output_limit: int) -> subprocess.CompletedProcess[bytes]:
    """Drain both pipes without permitting unbounded capture or indefinite waits."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={"PATH": "/usr/bin:/bin", "LANG": "C"}) as process:
        assert process.stdout is not None and process.stderr is not None
        output = {process.stdout: bytearray(), process.stderr: bytearray()}
        total = 0
        try:
            with selectors.DefaultSelector() as selector:
                for pipe in output:
                    selector.register(pipe, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(65_536, output_limit - total + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > output_limit:
                            raise OutputLimitExceeded("subprocess output exceeded byte budget")
                        output[key.fileobj].extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            returncode = process.wait(timeout=remaining)
            return subprocess.CompletedProcess(command, returncode, bytes(output[process.stdout]),
                                               bytes(output[process.stderr]))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
