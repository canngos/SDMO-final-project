"""Intentionally small cloud service for the legacy baseline."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from cryptography.exceptions import InvalidTag
from fastapi import FastAPI, HTTPException, Query, Request, status
from pydantic import ValidationError

from temperature_pqc.config import CloudSettings
from temperature_pqc.crypto import LegacyCloudKeyPair, MlKemCloudKeyPair
from temperature_pqc.models import (
    IngestEnvelope,
    MlKemEncryptedEnvelope,
    StoreResult,
    TemperatureReading,
)
from temperature_pqc.storage import ReadingStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.cloud")


def create_app(settings: CloudSettings | None = None) -> FastAPI:
    resolved = settings or CloudSettings.from_env()
    store = ReadingStore(resolved.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        application.state.rsa_key_pair = LegacyCloudKeyPair()
        application.state.mlkem_key_pair = MlKemCloudKeyPair.load_or_create(
            resolved.mlkem_seed_path
        )
        LOGGER.info(
            "event=cloud_started mlkem_key_id=%s rsa_ingest=%s",
            application.state.mlkem_key_pair.key_id,
            resolved.allow_rsa_ingest,
        )
        yield
        LOGGER.info("event=cloud_stopped")

    application = FastAPI(title="Dual-Protocol Temperature Cloud", version="0.2.0", lifespan=lifespan)
    application.state.store = store
    application.state.settings = resolved

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/crypto/public-key")
    async def public_key(request: Request) -> dict[str, object]:
        if not request.app.state.settings.allow_rsa_ingest:
            LOGGER.warning("event=rsa_public_key_request_rejected reason=rsa_ingest_disabled")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="RSA ingestion is disabled",
            )
        document = request.app.state.rsa_key_pair.public_document()
        LOGGER.info("event=rsa_public_key_served key_id=%s", document.key_id)
        return document.model_dump()

    @application.get("/v2/crypto/public-key")
    async def mlkem_public_key(request: Request) -> dict[str, object]:
        document = request.app.state.mlkem_key_pair.public_document()
        LOGGER.info("event=mlkem_public_key_served key_id=%s", document.key_id)
        return document.model_dump()

    @application.post("/v1/readings", response_model=StoreResult)
    async def store_reading(envelope: IngestEnvelope, request: Request) -> StoreResult:
        LOGGER.info(
            "event=cloud_envelope_received message_id=%s version=%s alg=%s key_id=%s",
            envelope.message_id,
            envelope.version,
            envelope.alg,
            envelope.key_id,
        )
        if isinstance(envelope, MlKemEncryptedEnvelope):
            key_pair = request.app.state.mlkem_key_pair
            security_mode = "mlkem"
        else:
            if not request.app.state.settings.allow_rsa_ingest:
                LOGGER.warning(
                    "event=cloud_envelope_rejected message_id=%s reason=rsa_ingest_disabled",
                    envelope.message_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="RSA ingestion is disabled",
                )
            key_pair = request.app.state.rsa_key_pair
            security_mode = "rsa"
        LOGGER.info(
            "event=cloud_crypto_selected message_id=%s security_mode=%s",
            envelope.message_id,
            security_mode,
        )
        try:
            plaintext = key_pair.decrypt(envelope)
            reading = TemperatureReading.model_validate(json.loads(plaintext))
            if reading.message_id != envelope.message_id:
                raise ValueError("message_id mismatch")
        except (InvalidTag, ValueError, ValidationError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "event=cloud_envelope_rejected message_id=%s security_mode=%s error_type=%s",
                envelope.message_id,
                security_mode,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid encrypted reading",
            ) from None
        LOGGER.info(
            "event=cloud_payload_validated message_id=%s sensor_id=%s security_mode=%s",
            reading.message_id,
            reading.sensor_id,
            security_mode,
        )
        if not request.app.state.store.insert(reading, security_mode=security_mode):
            LOGGER.warning(
                "event=cloud_reading_duplicate message_id=%s security_mode=%s",
                reading.message_id,
                security_mode,
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="duplicate message_id")
        LOGGER.info(
            "event=cloud_reading_stored message_id=%s sensor_id=%s security_mode=%s key_id=%s",
            reading.message_id,
            reading.sensor_id,
            security_mode,
            envelope.key_id,
        )
        return StoreResult(message_id=reading.message_id, security_mode=security_mode)

    @application.get("/v1/readings")
    async def readings(
        request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> list[dict[str, object]]:
        LOGGER.info("event=cloud_readings_requested limit=%s", limit)
        return request.app.state.store.list_recent(limit)

    return application


app = create_app()
