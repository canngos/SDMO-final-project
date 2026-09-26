"""Minimal SQLite repository for cloud readings."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from temperature_pqc.models import TemperatureReading

LOGGER = logging.getLogger("temperature_pqc.storage")


class ReadingStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS readings (
                    message_id TEXT PRIMARY KEY,
                    sensor_id TEXT NOT NULL,
                    temperature_c REAL NOT NULL,
                    measured_at TEXT NOT NULL,
                    security_mode TEXT NOT NULL,
                    cloud_received_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(readings)").fetchall()
            }
            if "received_at" in columns and "cloud_received_at" not in columns:
                connection.execute(
                    "ALTER TABLE readings RENAME COLUMN received_at TO cloud_received_at"
                )
                LOGGER.info(
                    "event=database_schema_migrated "
                    "change=received_at_to_cloud_received_at"
                )
        LOGGER.info("event=database_ready path=%s", self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def ready(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def insert(self, reading: TemperatureReading, security_mode: str = "rsa") -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO readings
                        (message_id, sensor_id, temperature_c, measured_at,
                         security_mode, cloud_received_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(reading.message_id),
                        reading.sensor_id,
                        reading.temperature_c,
                        reading.measured_at.isoformat(),
                        security_mode,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            LOGGER.info(
                "event=reading_persisted message_id=%s security_mode=%s",
                reading.message_id,
                security_mode,
            )
            return True
        except sqlite3.IntegrityError:
            LOGGER.warning(
                "event=reading_persist_conflict message_id=%s",
                reading.message_id,
            )
            return False

    def list_recent(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, sensor_id, temperature_c, measured_at,
                       security_mode, cloud_received_at
                FROM readings
                ORDER BY cloud_received_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        LOGGER.info("event=readings_loaded count=%s limit=%s", len(rows), limit)
        return [self._with_delivery_delay(dict(row)) for row in rows]

    @staticmethod
    def _with_delivery_delay(row: dict[str, object]) -> dict[str, object]:
        measured_at = datetime.fromisoformat(str(row["measured_at"]))
        cloud_received_at = datetime.fromisoformat(str(row["cloud_received_at"]))
        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=UTC)
        if cloud_received_at.tzinfo is None:
            # Legacy SQLite CURRENT_TIMESTAMP values were stored as UTC without an offset.
            cloud_received_at = cloud_received_at.replace(tzinfo=UTC)
        row["delivery_delay_seconds"] = round(
            (cloud_received_at - measured_at).total_seconds(),
            3,
        )
        return row
