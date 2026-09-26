"""Edge gateway with explicit cryptographic modes and a durable outbox."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sqlite3
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
from temperature_pqc.outbox import GatewayOutbox, OutboxFullError, PendingReading

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.gateway")


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    return type(exc).__name__


async def _deliver_reading(
    application: FastAPI,
    client: httpx.AsyncClient,
    reading: TemperatureReading,
) -> httpx.Response:
    settings: GatewaySettings = application.state.settings
    LOGGER.info(
        "event=cloud_key_request message_id=%s crypto_mode=%s",
        reading.message_id,
        settings.crypto_mode,
    )
    key_version = "v2" if settings.crypto_mode == "mlkem" else "v1"
    key_response = await client.get(
        f"{settings.cloud_url}/{key_version}/crypto/public-key"
    )
    key_response.raise_for_status()

    if settings.crypto_mode == "mlkem":
        try:
            key_payload = key_response.json()
            mlkem_document = MlKemPublicKeyDocument.model_validate(key_payload)
        except (ValidationError, ValueError) as exc:
            raise MlKemPublicKeyError("invalid ML-KEM public-key response") from exc
        if settings.expected_mlkem_key_id is None:
            raise MlKemPublicKeyError("ML-KEM key pin is not configured")
        envelope = encrypt_for_cloud_mlkem(
            mlkem_document,
            settings.expected_mlkem_key_id,
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
        settings.crypto_mode,
        public_key_id,
    )
    LOGGER.info(
        "event=delivery_attempt message_id=%s alg=%s key_id=%s",
        reading.message_id,
        envelope.alg,
        envelope.key_id,
    )
    response = await client.post(
        f"{settings.cloud_url}/v1/readings",
        json=envelope.model_dump(mode="json"),
    )
    if response.status_code == status.HTTP_409_CONFLICT:
        LOGGER.info(
            "event=duplicate_treated_as_delivered message_id=%s crypto_mode=%s",
            reading.message_id,
            settings.crypto_mode,
        )
        return response
    response.raise_for_status()
    return response


async def _schedule_retry(
    application: FastAPI,
    pending: PendingReading,
    exc: Exception,
) -> None:
    settings: GatewaySettings = application.state.settings
    category = _failure_category(exc)
    try:
        retry = await asyncio.to_thread(
            application.state.outbox.schedule_retry,
            str(pending.reading.message_id),
            category,
            settings.retry_initial_seconds,
            settings.retry_max_seconds,
        )
    except sqlite3.Error:
        LOGGER.exception(
            "event=retry_schedule_failed message_id=%s",
            pending.reading.message_id,
        )
        await asyncio.sleep(settings.outbox_poll_seconds)
        return
    if retry is None:
        return
    attempt_count, delay = retry
    LOGGER.warning(
        "event=delivery_retry_scheduled message_id=%s attempt_count=%s "
        "delay_seconds=%s error_category=%s crypto_mode=%s",
        pending.reading.message_id,
        attempt_count,
        delay,
        category,
        settings.crypto_mode,
    )


async def _outbox_worker(application: FastAPI) -> None:
    settings: GatewaySettings = application.state.settings
    outbox: GatewayOutbox = application.state.outbox
    wakeup: asyncio.Event = application.state.outbox_wakeup
    LOGGER.info("event=outbox_worker_started crypto_mode=%s", settings.crypto_mode)
    async with httpx.AsyncClient(
        transport=application.state.transport,
        timeout=settings.timeout_seconds,
    ) as client:
        while True:
            wakeup.clear()
            try:
                pending = await asyncio.to_thread(outbox.next_due)
            except sqlite3.Error:
                LOGGER.exception("event=outbox_read_failed")
                await asyncio.sleep(settings.outbox_poll_seconds)
                continue
            if pending is None:
                try:
                    await asyncio.wait_for(
                        wakeup.wait(),
                        timeout=settings.outbox_poll_seconds,
                    )
                except TimeoutError:
                    pass
                continue

            try:
                cloud_response = await _deliver_reading(
                    application,
                    client,
                    pending.reading,
                )
            except MlKemPublicKeyError as exc:
                application.state.crypto_ready = False
                LOGGER.critical(
                    "event=mlkem_key_validation_failed message_id=%s error_type=%s",
                    pending.reading.message_id,
                    type(exc).__name__,
                )
                await _schedule_retry(application, pending, exc)
            except (httpx.HTTPError, ValueError) as exc:
                LOGGER.warning(
                    "event=gateway_forward_failed message_id=%s error_category=%s",
                    pending.reading.message_id,
                    _failure_category(exc),
                )
                await _schedule_retry(application, pending, exc)
            else:
                application.state.crypto_ready = True
                try:
                    await asyncio.to_thread(
                        outbox.mark_delivered,
                        str(pending.reading.message_id),
                    )
                except sqlite3.Error:
                    LOGGER.exception(
                        "event=outbox_delivery_commit_failed message_id=%s",
                        pending.reading.message_id,
                    )
                    await asyncio.sleep(settings.outbox_poll_seconds)
                    continue
                LOGGER.info(
                    "event=delivery_succeeded message_id=%s status_code=%s "
                    "crypto_mode=%s",
                    pending.reading.message_id,
                    cloud_response.status_code,
                    settings.crypto_mode,
                )


def create_app(
    settings: GatewaySettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or GatewaySettings.from_env()
    outbox = GatewayOutbox(
        resolved.outbox_database_path,
        resolved.outbox_max_pending,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        outbox.initialize()
        if resolved.crypto_mode == "mlkem":
            ensure_mlkem_available()
        application.state.crypto_ready = True
        application.state.outbox_wakeup = asyncio.Event()
        worker = asyncio.create_task(_outbox_worker(application))
        LOGGER.info(
            "event=gateway_started cloud_url=%s crypto_mode=%s expected_key_id=%s",
            resolved.cloud_url,
            resolved.crypto_mode,
            resolved.expected_mlkem_key_id or "not-configured",
        )
        yield
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        LOGGER.info("event=gateway_stopped")

    application = FastAPI(
        title="Temperature Edge Gateway",
        version="0.3.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.transport = transport
    application.state.outbox = outbox
    application.state.crypto_ready = resolved.crypto_mode == "rsa"

    @application.get("/health")
    async def health() -> dict[str, str | int]:
        if not application.state.crypto_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="gateway cryptographic configuration is not ready",
            )
        outbox_status = await asyncio.to_thread(outbox.status)
        return {
            "status": "ok",
            "crypto_mode": resolved.crypto_mode,
            "outbox_pending": int(outbox_status["pending"]),
        }

    @application.get("/v1/outbox/status")
    async def outbox_status() -> dict[str, int | float | None]:
        return await asyncio.to_thread(outbox.status)

    @application.post("/v1/readings", status_code=status.HTTP_202_ACCEPTED)
    async def receive(reading: TemperatureReading) -> dict[str, str | bool]:
        LOGGER.info(
            "event=gateway_reading_received message_id=%s sensor_id=%s crypto_mode=%s",
            reading.message_id,
            reading.sensor_id,
            resolved.crypto_mode,
        )
        try:
            inserted = await asyncio.to_thread(outbox.enqueue, reading)
        except OutboxFullError:
            LOGGER.error(
                "event=outbox_full message_id=%s max_pending=%s",
                reading.message_id,
                resolved.outbox_max_pending,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="gateway outbox is full",
            ) from None
        except sqlite3.Error:
            LOGGER.exception(
                "event=outbox_write_failed message_id=%s",
                reading.message_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="gateway outbox is unavailable",
            ) from None

        application.state.outbox_wakeup.set()
        return {
            "status": "queued",
            "message_id": str(reading.message_id),
            "already_queued": not inserted,
        }

    return application


app = create_app()
