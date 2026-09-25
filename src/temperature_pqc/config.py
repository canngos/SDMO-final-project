"""Small environment-based configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass


def env_positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True)
class CloudSettings:
    database_path: str
    mlkem_seed_path: str = "data/mlkem-768.seed"
    allow_rsa_ingest: bool = True

    @classmethod
    def from_env(cls) -> CloudSettings:
        return cls(
            database_path=os.getenv("DATABASE_PATH", "data/readings.db"),
            mlkem_seed_path=os.getenv("MLKEM_SEED_PATH", "data/mlkem-768.seed"),
            allow_rsa_ingest=env_bool("ALLOW_RSA_INGEST", True),
        )


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
