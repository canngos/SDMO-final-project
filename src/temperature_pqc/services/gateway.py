"""Intentionally small edge gateway for the legacy baseline."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import ValidationError

from temperature_pqc.config import GatewaySettings
from temperature_pqc.crypto import (
    MlKemPublicKeyError,
    encrypt_for_cloud,
    encrypt_for_cloud_mlkem,
    ensure_mlkem_available,
)
from temperature_pqc.models import (
    MlKemPublicKeyDocument,
    PublicKeyDocument,
    TemperatureReading,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.gateway")


def create_app(
    settings: GatewaySettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or GatewaySettings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if resolved.crypto_mode == "mlkem":
            ensure_mlkem_available()
        application.state.crypto_ready = True
        LOGGER.info(
            "event=gateway_started cloud_url=%s crypto_mode=%s expected_key_id=%s",
            resolved.cloud_url,
            resolved.crypto_mode,
            resolved.expected_mlkem_key_id or "not-configured",
        )
        yield
        LOGGER.info("event=gateway_stopped")

    application = FastAPI(
        title="Legacy Temperature Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.crypto_ready = resolved.crypto_mode == "rsa"

    @application.get("/health")
    async def health() -> dict[str, str]:
        if not application.state.crypto_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="gateway cryptographic configuration is not ready",
            )
        return {"status": "ok", "crypto_mode": resolved.crypto_mode}

    @application.post("/v1/readings", status_code=status.HTTP_202_ACCEPTED)
    async def receive(reading: TemperatureReading) -> dict[str, str]:
        if not application.state.crypto_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="gateway cryptographic configuration is not ready",
            )
        LOGGER.info(
            "event=gateway_reading_received message_id=%s sensor_id=%s crypto_mode=%s",
            reading.message_id,
            reading.sensor_id,
            resolved.crypto_mode,
        )
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=resolved.timeout_seconds,
            ) as client:
                LOGGER.info(
                    "event=cloud_key_request message_id=%s crypto_mode=%s",
                    reading.message_id,
                    resolved.crypto_mode,
                )
                key_version = "v2" if resolved.crypto_mode == "mlkem" else "v1"
                key_response = await client.get(
                    f"{resolved.cloud_url}/{key_version}/crypto/public-key"
                )
                key_response.raise_for_status()

                if resolved.crypto_mode == "mlkem":
                    try:
                        mlkem_document = MlKemPublicKeyDocument.model_validate(
                            key_response.json()
                        )
                    except ValidationError as exc:
                        raise MlKemPublicKeyError(
                            "invalid ML-KEM public-key response"
                        ) from exc
                    if resolved.expected_mlkem_key_id is None:
                        raise MlKemPublicKeyError("ML-KEM key pin is not configured")
                    envelope = encrypt_for_cloud_mlkem(
                        mlkem_document,
                        resolved.expected_mlkem_key_id,
                        reading.message_id,
                        reading.model_dump_json().encode("utf-8"),
                    )
                    public_key_id = mlkem_document.key_id
                else:
                    public_document = PublicKeyDocument.model_validate(key_response.json())
                    envelope = encrypt_for_cloud(
                        public_document,
                        reading.message_id,
                        reading.model_dump_json().encode("utf-8"),
                    )
                    public_key_id = public_document.key_id

                LOGGER.info(
                    "event=cloud_key_received message_id=%s crypto_mode=%s key_id=%s",
                    reading.message_id,
                    resolved.crypto_mode,
                    public_key_id,
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
        except MlKemPublicKeyError as exc:
            application.state.crypto_ready = False
            LOGGER.critical(
                "event=mlkem_key_validation_failed message_id=%s error_type=%s",
                reading.message_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="cloud ML-KEM key validation failed",
            ) from None
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
            "event=cloud_forward_succeeded message_id=%s status_code=%s crypto_mode=%s",
            reading.message_id,
            cloud_response.status_code,
            resolved.crypto_mode,
        )
        return {
            "status": "forwarded",
            "message_id": str(reading.message_id),
            "security_mode": resolved.crypto_mode,
        }

    return application


app = create_app()
