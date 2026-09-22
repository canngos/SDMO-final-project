"""Minimal SQLite repository for cloud readings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from temperature_pqc.models import TemperatureReading


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
                    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

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
                        (message_id, sensor_id, temperature_c, measured_at, security_mode)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(reading.message_id),
                        reading.sensor_id,
                        reading.temperature_c,
                        reading.measured_at.isoformat(),
                        security_mode,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def list_recent(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, sensor_id, temperature_c, measured_at,
                       security_mode, received_at
                FROM readings
                ORDER BY received_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
