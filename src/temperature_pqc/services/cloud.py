"""Intentionally small cloud service for the legacy baseline."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from cryptography.exceptions import InvalidTag
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from temperature_pqc.config import CloudSettings
from temperature_pqc.crypto import LegacyCloudKeyPair, MlKemCloudKeyPair
from temperature_pqc.models import (
    IngestEnvelope,
    MlKemEncryptedEnvelope,
    StoreResult,
    TemperatureReading,
)
from temperature_pqc.observability import MetricsRegistry, monitoring_payload
from temperature_pqc.storage import ReadingStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.cloud")


def create_app(settings: CloudSettings | None = None) -> FastAPI:
    resolved = settings or CloudSettings.from_env()
    store = ReadingStore(resolved.database_path)
    metrics = MetricsRegistry(
        [
            "envelopes_received_total",
            "rsa_envelopes_received_total",
            "mlkem_envelopes_received_total",
            "readings_persisted_total",
            "duplicate_deliveries_total",
            "validation_failures_total",
            "rsa_ingest_rejections_total",
        ]
    )

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
    application.state.metrics = metrics
    application.state.consecutive_validation_failures = 0

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    def readiness_payload(request: Request) -> tuple[int, dict[str, object]]:
        checks = {
            "database_accessible": request.app.state.store.ready(),
            "rsa_key_initialized": hasattr(request.app.state, "rsa_key_pair"),
            "mlkem_key_initialized": hasattr(request.app.state, "mlkem_key_pair"),
            "configuration_loaded": True,
        }
        ready = all(checks.values())
        return (
            status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "status": "ready" if ready else "not_ready",
                "checks": checks,
            },
        )

    @application.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        status_code, payload = readiness_payload(request)
        if status_code != status.HTTP_200_OK:
            LOGGER.warning("event=cloud_readiness_failed checks=%s", payload["checks"])
        return JSONResponse(status_code=status_code, content=payload)

    @application.get("/metrics")
    async def service_metrics(request: Request) -> dict[str, object]:
        payload = request.app.state.metrics.snapshot()
        try:
            payload.update(request.app.state.store.metrics())
            payload["database_metrics_available"] = True
        except Exception:
            LOGGER.exception("event=cloud_metrics_database_failed")
            payload["database_metrics_available"] = False
        payload["allow_rsa_ingest"] = request.app.state.settings.allow_rsa_ingest
        return payload

    @application.get("/monitoring")
    async def monitoring(request: Request) -> dict[str, object]:
        ready_status, _ = readiness_payload(request)
        warnings: list[dict[str, str]] = []
        if ready_status != status.HTTP_200_OK:
            warnings.append(
                {
                    "code": "service_not_ready",
                    "severity": "warning",
                    "message": "cloud readiness checks are failing",
                }
            )
        if (
            request.app.state.consecutive_validation_failures
            >= request.app.state.settings.validation_failure_warning_threshold
        ):
            warnings.append(
                {
                    "code": "repeated_validation_failures",
                    "severity": "warning",
                    "message": "cloud has rejected repeated encrypted envelopes",
                }
            )
        return monitoring_payload(warnings)

    @application.get("/v1/crypto/public-key")
    async def public_key(request: Request) -> dict[str, object]:
        if not request.app.state.settings.allow_rsa_ingest:
            request.app.state.metrics.increment("rsa_ingest_rejections_total")
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
        request.app.state.metrics.increment("envelopes_received_total")
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
            request.app.state.metrics.increment("mlkem_envelopes_received_total")
        else:
            request.app.state.metrics.increment("rsa_envelopes_received_total")
            if not request.app.state.settings.allow_rsa_ingest:
                request.app.state.metrics.increment("rsa_ingest_rejections_total")
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
            request.app.state.metrics.increment("validation_failures_total")
            request.app.state.consecutive_validation_failures += 1
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
            request.app.state.metrics.increment("duplicate_deliveries_total")
            request.app.state.consecutive_validation_failures = 0
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
        request.app.state.metrics.increment("readings_persisted_total")
        request.app.state.consecutive_validation_failures = 0
        return StoreResult(message_id=reading.message_id, security_mode=security_mode)

    @application.get("/v1/readings")
    async def readings(
        request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> list[dict[str, object]]:
        LOGGER.info("event=cloud_readings_requested limit=%s", limit)
        return request.app.state.store.list_recent(limit)

    return application


app = create_app()
