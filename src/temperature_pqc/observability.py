"""Small in-process observability helpers for the demo services."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from threading import Lock


class MetricsRegistry:
    """Thread-safe counters plus process uptime metadata."""

    def __init__(self, counters: list[str]) -> None:
        self._started_at_monotonic = time.monotonic()
        self._started_at_iso = datetime.now(UTC).isoformat()
        self._counters = {name: 0 for name in counters}
        self._lock = Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def value(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def snapshot(self) -> dict[str, int | float | str]:
        with self._lock:
            counters = dict(self._counters)
        return {
            "start_time": self._started_at_iso,
            "uptime_seconds": round(
                max(0.0, time.monotonic() - self._started_at_monotonic),
                3,
            ),
            **counters,
        }


def monitoring_payload(warnings: list[dict[str, str]]) -> dict[str, object]:
    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
    }
