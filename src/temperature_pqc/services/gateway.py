"""Intentionally small edge gateway for the legacy baseline."""

from __future__ import annotations

import logging
import os

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
    application = FastAPI(title="Legacy Temperature Gateway", version="0.1.0")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/readings", status_code=status.HTTP_202_ACCEPTED)
    async def receive(reading: TemperatureReading) -> dict[str, str]:
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=resolved.timeout_seconds,
            ) as client:
                key_response = await client.get(f"{resolved.cloud_url}/v1/crypto/public-key")
                key_response.raise_for_status()
                public_document = PublicKeyDocument.model_validate(key_response.json())
                envelope = encrypt_for_cloud(
                    public_document,
                    reading.message_id,
                    reading.model_dump_json().encode("utf-8"),
                )
                cloud_response = await client.post(
                    f"{resolved.cloud_url}/v1/readings",
                    json=envelope.model_dump(mode="json"),
                )
                cloud_response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            LOGGER.exception("failed to forward reading %s", reading.message_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="cloud forwarding failed",
            ) from None
        LOGGER.info("forwarded reading %s", reading.message_id)
        return {"status": "forwarded", "message_id": str(reading.message_id)}

    return application


app = create_app()

