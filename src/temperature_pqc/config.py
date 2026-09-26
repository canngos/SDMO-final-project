"""Small environment-based configuration helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal


def env_positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def env_positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
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
    crypto_mode: Literal["rsa", "mlkem"] = "rsa"
    expected_mlkem_key_id: str | None = None
    outbox_database_path: str = "data/gateway-outbox.db"
    outbox_max_pending: int = 1000
    retry_initial_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    outbox_poll_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.crypto_mode not in {"rsa", "mlkem"}:
            raise ValueError("CRYPTO_MODE must be either 'rsa' or 'mlkem'")
        if self.crypto_mode == "mlkem":
            if self.expected_mlkem_key_id is None:
                raise ValueError("EXPECTED_MLKEM_KEY_ID is required in ML-KEM mode")
            if re.fullmatch(r"[a-f0-9]{64}", self.expected_mlkem_key_id) is None:
                raise ValueError(
                    "EXPECTED_MLKEM_KEY_ID must be a 64-character lowercase SHA-256 fingerprint"
                )
        if self.outbox_max_pending <= 0:
            raise ValueError("GATEWAY_OUTBOX_MAX_PENDING must be greater than zero")
        if self.retry_initial_seconds <= 0 or self.retry_max_seconds <= 0:
            raise ValueError("gateway retry intervals must be greater than zero")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError(
                "GATEWAY_RETRY_MAX_SECONDS must be greater than or equal to the initial interval"
            )
        if self.outbox_poll_seconds <= 0:
            raise ValueError("GATEWAY_OUTBOX_POLL_SECONDS must be greater than zero")

    @classmethod
    def from_env(cls) -> GatewaySettings:
        crypto_mode = os.getenv("CRYPTO_MODE", "rsa").strip().lower()
        expected_key_id = os.getenv("EXPECTED_MLKEM_KEY_ID", "").strip().lower() or None
        return cls(
            cloud_url=os.getenv("CLOUD_URL", "http://localhost:8000").rstrip("/"),
            timeout_seconds=env_positive_float("HTTP_TIMEOUT_SECONDS", 5.0),
            crypto_mode=crypto_mode,
            expected_mlkem_key_id=expected_key_id,
            outbox_database_path=os.getenv(
                "GATEWAY_OUTBOX_PATH", "data/gateway-outbox.db"
            ),
            outbox_max_pending=env_positive_int("GATEWAY_OUTBOX_MAX_PENDING", 1000),
            retry_initial_seconds=env_positive_float(
                "GATEWAY_RETRY_INITIAL_SECONDS", 1.0
            ),
            retry_max_seconds=env_positive_float("GATEWAY_RETRY_MAX_SECONDS", 30.0),
            outbox_poll_seconds=env_positive_float(
                "GATEWAY_OUTBOX_POLL_SECONDS", 0.5
            ),
        )


@dataclass(frozen=True)
class SensorSettings:
    gateway_url: str
    sensor_id: str
    interval_seconds: float
    timeout_seconds: float
    retry_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> SensorSettings:
        return cls(
            gateway_url=os.getenv("GATEWAY_URL", "http://localhost:8001").rstrip("/"),
            sensor_id=os.getenv("SENSOR_ID", "sensor-01"),
            interval_seconds=env_positive_float("SENSOR_INTERVAL_SECONDS", 3.0),
            timeout_seconds=env_positive_float("HTTP_TIMEOUT_SECONDS", 5.0),
            retry_seconds=env_positive_float("SENSOR_RETRY_SECONDS", 1.0),
        )
