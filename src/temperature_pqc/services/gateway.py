"""Intentionally small edge gateway for the legacy baseline."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, status

from temperature_pqc.config import GatewaySettings
from temperature_pqc.crypto import encrypt_for_cloud
from temperature_pqc.models import PublicKeyDocument, TemperatureReading

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.gateway")


def create_app(
    settings: GatewaySettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or GatewaySettings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        LOGGER.info(
            "event=gateway_started cloud_url=%s crypto_mode=rsa",
            resolved.cloud_url,
        )
        yield
        LOGGER.info("event=gateway_stopped")

    application = FastAPI(
        title="Legacy Temperature Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/readings", status_code=status.HTTP_202_ACCEPTED)
    async def receive(reading: TemperatureReading) -> dict[str, str]:
        LOGGER.info(
            "event=gateway_reading_received message_id=%s sensor_id=%s",
            reading.message_id,
            reading.sensor_id,
        )
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=resolved.timeout_seconds,
            ) as client:
                LOGGER.info(
                    "event=cloud_key_request message_id=%s crypto_mode=rsa",
                    reading.message_id,
                )
                key_response = await client.get(f"{resolved.cloud_url}/v1/crypto/public-key")
                key_response.raise_for_status()
                public_document = PublicKeyDocument.model_validate(key_response.json())
                LOGGER.info(
                    "event=cloud_key_received message_id=%s crypto_mode=rsa key_id=%s",
                    reading.message_id,
                    public_document.key_id,
                )
                envelope = encrypt_for_cloud(
                    public_document,
                    reading.message_id,
                    reading.model_dump_json().encode("utf-8"),
                )
                LOGGER.info(
                    "event=cloud_forward_attempt message_id=%s alg=%s key_id=%s",
                    reading.message_id,
                    envelope.alg,
                    envelope.key_id,
                )
                cloud_response = await client.post(
                    f"{resolved.cloud_url}/v1/readings",
                    json=envelope.model_dump(mode="json"),
                )
                cloud_response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.exception(
                "event=gateway_forward_failed message_id=%s error_type=%s",
                reading.message_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="cloud forwarding failed",
            ) from None
        LOGGER.info(
            "event=cloud_forward_succeeded message_id=%s status_code=%s",
            reading.message_id,
            cloud_response.status_code,
        )
        return {"status": "forwarded", "message_id": str(reading.message_id)}

    return application


app = create_app()
