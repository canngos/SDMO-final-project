"""Validated API models shared by the gateway and cloud."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class TemperatureReading(BaseModel):
    message_id: UUID = Field(default_factory=uuid4)
    sensor_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]
    temperature_c: Annotated[float, Field(ge=-80.0, le=150.0)]
    measured_at: datetime

    @field_validator("measured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("measured_at must include a timezone")
        return value.astimezone(UTC)


class PublicKeyDocument(BaseModel):
    version: Literal[1] = 1
    alg: Literal["RSA-OAEP-2048"] = "RSA-OAEP-2048"
    key_id: Annotated[str, Field(min_length=16, max_length=64)]
    public_key_pem: str


class EncryptedEnvelope(BaseModel):
    version: Literal[1] = 1
    alg: Literal["RSA-OAEP-2048+AES-256-GCM"] = "RSA-OAEP-2048+AES-256-GCM"
    key_id: Annotated[str, Field(min_length=16, max_length=64)]
    message_id: UUID
    wrapped_key: str
    nonce: str
    ciphertext: str


class StoreResult(BaseModel):
    status: Literal["stored"] = "stored"
    message_id: UUID
    security_mode: Literal["rsa"] = "rsa"
