"""Structured phase logging for the Code Agent."""

from __future__ import annotations

import time


def log(phase: str, msg: str) -> None:
    print(f"[{phase}] {msg}", flush=True)


class Timer:
    """Context manager that logs elapsed time on exit."""

    def __init__(self, phase: str, label: str) -> None:
        self._phase = phase
        self._label = label
        self._start: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        log(self._phase, f"{self._label} — started")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = time.monotonic() - self._start
        if exc_type is None:
            log(self._phase, f"{self._label} — done ({elapsed:.1f}s)")
        else:
            log("ERROR", f"{self._label} — failed after {elapsed:.1f}s: {exc_val}")
