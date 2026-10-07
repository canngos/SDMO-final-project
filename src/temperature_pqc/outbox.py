"""Durable SQLite store-and-forward queue for the edge gateway."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from temperature_pqc.models import TemperatureReading

LOGGER = logging.getLogger("temperature_pqc.outbox")


class OutboxFullError(RuntimeError):
    """The configured pending-reading capacity has been reached."""


@dataclass(frozen=True)
class PendingReading:
    reading: TemperatureReading
    attempt_count: int
    created_at: float


class GatewayOutbox:
    def __init__(self, database_path: str, max_pending: int) -> None:
        self.database_path = database_path
        self.max_pending = max_pending

    def initialize(self) -> None:
        if self.database_path != ":memory:":
            path = Path(self.database_path)
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_outbox (
                    message_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_outbox_metrics (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    total_retries INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO gateway_outbox_metrics
                    (singleton, total_retries)
                VALUES (1, 0)
                """
            )
            connection.execute(
                """
                UPDATE gateway_outbox
                SET next_attempt_at = ?
                WHERE next_attempt_at > ?
                """,
                (time.time(), time.time()),
            )
        if self.database_path != ":memory:":
            try:
                os.chmod(self.database_path, 0o600)
            except OSError:
                LOGGER.warning(
                    "event=outbox_permission_update_failed path=%s",
                    self.database_path,
                )
        LOGGER.info(
            "event=outbox_ready path=%s max_pending=%s",
            self.database_path,
            self.max_pending,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def enqueue(self, reading: TemperatureReading) -> bool:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM gateway_outbox WHERE message_id = ?",
                (str(reading.message_id),),
            ).fetchone()
            if existing is not None:
                LOGGER.info(
                    "event=reading_already_queued message_id=%s",
                    reading.message_id,
                )
                return False
            count = connection.execute(
                "SELECT COUNT(*) FROM gateway_outbox"
            ).fetchone()[0]
            if count >= self.max_pending:
                raise OutboxFullError("gateway outbox is full")
            connection.execute(
                """
                INSERT INTO gateway_outbox
                    (message_id, payload_json, created_at, next_attempt_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(reading.message_id),
                    reading.model_dump_json(),
                    now,
                    now,
                ),
            )
        LOGGER.info(
            "event=reading_queued message_id=%s sensor_id=%s",
            reading.message_id,
            reading.sensor_id,
        )
        return True

    def next_due(self, now: float | None = None) -> PendingReading | None:
        effective_now = time.time() if now is None else now
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json, attempt_count, created_at
                FROM gateway_outbox
                WHERE next_attempt_at <= ?
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """,
                (effective_now,),
            ).fetchone()
        if row is None:
            return None
        return PendingReading(
            reading=TemperatureReading.model_validate_json(row["payload_json"]),
            attempt_count=row["attempt_count"],
            created_at=row["created_at"],
        )

    def mark_delivered(self, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM gateway_outbox WHERE message_id = ?",
                (message_id,),
            )
        LOGGER.info("event=outbox_delivery_completed message_id=%s", message_id)

    def schedule_retry(
        self,
        message_id: str,
        error_category: str,
        initial_seconds: float,
        maximum_seconds: float,
    ) -> tuple[int, float] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempt_count FROM gateway_outbox WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            previous_attempts = int(row["attempt_count"])
            delay = min(initial_seconds * (2 ** min(previous_attempts, 20)), maximum_seconds)
            attempt_count = previous_attempts + 1
            connection.execute(
                """
                UPDATE gateway_outbox
                SET attempt_count = ?, next_attempt_at = ?, last_error = ?
                WHERE message_id = ?
                """,
                (
                    attempt_count,
                    time.time() + delay,
                    error_category[:80],
                    message_id,
                ),
            )
            connection.execute(
                """
                UPDATE gateway_outbox_metrics
                SET total_retries = total_retries + 1
                WHERE singleton = 1
                """
            )
        return attempt_count, delay

    def status(self) -> dict[str, int | float | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS pending, MIN(created_at) AS oldest FROM gateway_outbox"
            ).fetchone()
            metrics = connection.execute(
                """
                SELECT total_retries
                FROM gateway_outbox_metrics
                WHERE singleton = 1
                """
            ).fetchone()
        oldest = row["oldest"]
        oldest_seconds = (
            round(max(0.0, time.time() - float(oldest)), 3)
            if oldest is not None
            else None
        )
        return {
            "pending": int(row["pending"]),
            "oldest_pending_seconds": oldest_seconds,
            "total_retries": int(metrics["total_retries"]),
        }
