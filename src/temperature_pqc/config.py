"""Small environment-based configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass


def env_positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class CloudSettings:
    database_path: str

    @classmethod
    def from_env(cls) -> CloudSettings:
        return cls(database_path=os.getenv("DATABASE_PATH", "data/readings.db"))


@dataclass(frozen=True)
class GatewaySettings:
    cloud_url: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> GatewaySettings:
        return cls(
            cloud_url=os.getenv("CLOUD_URL", "http://localhost:8000").rstrip("/"),
            timeout_seconds=env_positive_float("HTTP_TIMEOUT_SECONDS", 5.0),
        )


@dataclass(frozen=True)
class SensorSettings:
    gateway_url: str
    sensor_id: str
    interval_seconds: float
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> SensorSettings:
        return cls(
            gateway_url=os.getenv("GATEWAY_URL", "http://localhost:8001").rstrip("/"),
            sensor_id=os.getenv("SENSOR_ID", "sensor-01"),
            interval_seconds=env_positive_float("SENSOR_INTERVAL_SECONDS", 3.0),
            timeout_seconds=env_positive_float("HTTP_TIMEOUT_SECONDS", 5.0),
        )
