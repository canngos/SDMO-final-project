"""Validated API models shared by the gateway and cloud."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

RSA_ALGORITHM = "RSA-OAEP-2048+AES-256-GCM"
MLKEM_ALGORITHM = "ML-KEM-768+HKDF-SHA256+AES-256-GCM"

KeyId = Annotated[str, Field(min_length=16, max_length=64, pattern=r"^[a-f0-9]+$")]
Base64Value = Annotated[
    str,
    Field(min_length=4, max_length=8192, pattern=r"^[A-Za-z0-9+/]*={0,2}$"),
]


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


class RsaPublicKeyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    alg: Literal["RSA-OAEP-2048"] = "RSA-OAEP-2048"
    key_id: KeyId
    public_key_pem: str


class MlKemPublicKeyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    alg: Literal["ML-KEM-768"] = "ML-KEM-768"
    key_id: KeyId
    public_key_b64: Base64Value


class RsaEncryptedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    alg: Literal["RSA-OAEP-2048+AES-256-GCM"] = RSA_ALGORITHM
    key_id: KeyId
    message_id: UUID
    wrapped_key: Base64Value
    nonce: Base64Value
    ciphertext: Base64Value


class MlKemEncryptedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    alg: Literal["ML-KEM-768+HKDF-SHA256+AES-256-GCM"] = MLKEM_ALGORITHM
    key_id: KeyId
    message_id: UUID
    kem_ciphertext: Base64Value
    nonce: Base64Value
    ciphertext: Base64Value


IngestEnvelope = Annotated[
    RsaEncryptedEnvelope | MlKemEncryptedEnvelope,
    Field(discriminator="alg"),
]

# Compatibility aliases keep the stage-one gateway unchanged.
PublicKeyDocument = RsaPublicKeyDocument
EncryptedEnvelope = RsaEncryptedEnvelope


class StoreResult(BaseModel):
    status: Literal["stored"] = "stored"
    message_id: UUID
    security_mode: Literal["rsa", "mlkem"]
