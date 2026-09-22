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
from temperature_pqc.crypto import LegacyCloudKeyPair
from temperature_pqc.models import EncryptedEnvelope, StoreResult, TemperatureReading
from temperature_pqc.storage import ReadingStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.cloud")


def create_app(settings: CloudSettings | None = None) -> FastAPI:
    resolved = settings or CloudSettings.from_env()
    store = ReadingStore(resolved.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        application.state.key_pair = LegacyCloudKeyPair()
        LOGGER.info("cloud started")
        yield

    application = FastAPI(title="Legacy Temperature Cloud", version="0.1.0", lifespan=lifespan)
    application.state.store = store

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/crypto/public-key")
    async def public_key(request: Request) -> dict[str, object]:
        return request.app.state.key_pair.public_document().model_dump()

    @application.post("/v1/readings", response_model=StoreResult)
    async def store_reading(envelope: EncryptedEnvelope, request: Request) -> StoreResult:
        try:
            plaintext = request.app.state.key_pair.decrypt(envelope)
            reading = TemperatureReading.model_validate(json.loads(plaintext))
            if reading.message_id != envelope.message_id:
                raise ValueError("message_id mismatch")
        except (InvalidTag, ValueError, ValidationError, json.JSONDecodeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid encrypted reading",
            ) from None
        if not request.app.state.store.insert(reading):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="duplicate message_id")
        LOGGER.info("stored reading %s from %s", reading.message_id, reading.sensor_id)
        return StoreResult(message_id=reading.message_id)

    @application.get("/v1/readings")
    async def readings(
        request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> list[dict[str, object]]:
        return request.app.state.store.list_recent(limit)

    return application


app = create_app()

