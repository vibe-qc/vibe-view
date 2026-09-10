"""Performance profiling for vibe-view.

Measures rendering time, memory usage, and section access patterns
to identify bottlenecks in large-system visualization.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any


class ProfileResult:
    """Result of a profiling session."""

    def __init__(self, name: str):
        self.name = name
        self.start_time = time.perf_counter()
        self.end_time: float = 0.0
        self.metrics: dict[str, Any] = {}

    def stop(self) -> float:
        self.end_time = time.perf_counter()
        return self.elapsed

    @property
    def elapsed(self) -> float:
        end = self.end_time if self.end_time > 0 else time.perf_counter()
        return end - self.start_time

    def __repr__(self) -> str:
        return f"ProfileResult({self.name!r}, {self.elapsed:.3f}s)"


class Profiler:
    """Simple in-process profiler for vibe-view operations."""

    def __init__(self):
        self._results: list[ProfileResult] = []
        self._active: dict[str, ProfileResult] = {}

    def start(self, name: str) -> ProfileResult:
        """Begin timing an operation."""
        result = ProfileResult(name)
        self._active[name] = result
        return result

    def stop(self, name: str) -> ProfileResult:
        """Stop timing and record the result."""
        result = self._active.pop(name, None)
        if result is None:
            result = ProfileResult(name)
        result.stop()
        self._results.append(result)
        return result

    @contextmanager
    def measure(self, name: str):
        """Context manager for timing a block."""
        self.start(name)
        try:
            yield
        finally:
            self.stop(name)

    def get_stats(self) -> list[dict]:
        """Return all timing results as a list of dicts."""
        return [{"name": r.name, "elapsed_s": r.elapsed} for r in self._results]

    def get_summary(self) -> str:
        """Human-readable summary of all timings."""
        lines = ["Performance profile:"]
        total = sum(r.elapsed for r in self._results)
        for r in sorted(self._results, key=lambda x: -x.elapsed):
            pct = (r.elapsed / total * 100) if total > 0 else 0
            lines.append(f"  {r.name:<40s} {r.elapsed:8.3f}s ({pct:5.1f}%)")
        lines.append(f"  {'TOTAL':<40s} {total:8.3f}s")
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all results."""
        self._results.clear()
        self._active.clear()


# Global profiler instance
profiler = Profiler()


def profile(func):
    """Decorator: profile a function call."""
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with profiler.measure(func.__name__):
            return func(*args, **kwargs)

    return wrapper
