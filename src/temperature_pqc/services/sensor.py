"""Simulated temperature sensor with a raw-reading HTTP endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from temperature_pqc.config import SensorSettings
from temperature_pqc.models import TemperatureReading
from temperature_pqc.observability import MetricsRegistry, monitoring_payload

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.sensor")


class TemperatureSimulator:
    def __init__(self, sensor_id: str, initial_temperature: float = 21.0) -> None:
        self.sensor_id = sensor_id
        self.temperature = initial_temperature

    def sample(self) -> TemperatureReading:
        drifted = self.temperature + random.gauss(0.0, 0.25)
        self.temperature = round(min(35.0, max(10.0, drifted)), 2)
        reading = TemperatureReading(
            sensor_id=self.sensor_id,
            temperature_c=self.temperature,
            measured_at=datetime.now(UTC),
        )
        LOGGER.info(
            "event=reading_sampled message_id=%s sensor_id=%s",
            reading.message_id,
            reading.sensor_id,
        )
        return reading


async def _forward_readings(application: FastAPI) -> None:
    settings: SensorSettings = application.state.settings
    LOGGER.info(
        "event=sensor_forwarder_started sensor_id=%s gateway_url=%s interval_seconds=%s",
        settings.sensor_id,
        settings.gateway_url,
        settings.interval_seconds,
    )
    async with httpx.AsyncClient(
        transport=application.state.transport,
        timeout=settings.timeout_seconds,
    ) as client:
        while True:
            reading: TemperatureReading = application.state.latest_reading
            LOGGER.info(
                "event=sensor_forward_attempt message_id=%s gateway_url=%s",
                reading.message_id,
                settings.gateway_url,
            )
            try:
                response = await client.post(
                    f"{settings.gateway_url}/v1/readings",
                    json=reading.model_dump(mode="json"),
                )
                response.raise_for_status()
                application.state.metrics.increment("gateway_send_successes_total")
                application.state.consecutive_gateway_failures = 0
                LOGGER.info(
                    "event=sensor_forward_succeeded message_id=%s status_code=%s",
                    reading.message_id,
                    response.status_code,
                )
                await asyncio.sleep(settings.interval_seconds)
                application.state.latest_reading = application.state.sample_reading()
            except httpx.HTTPError as exc:
                application.state.metrics.increment("gateway_send_failures_total")
                application.state.consecutive_gateway_failures += 1
                LOGGER.warning(
                    "event=sensor_forward_failed message_id=%s error_type=%s",
                    reading.message_id,
                    type(exc).__name__,
                )
                LOGGER.info(
                    "event=sensor_retry_scheduled message_id=%s delay_seconds=%s",
                    reading.message_id,
                    settings.retry_seconds,
                )
                await asyncio.sleep(settings.retry_seconds)


def create_app(
    settings: SensorSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or SensorSettings.from_env()
    simulator = TemperatureSimulator(resolved.sensor_id)
    metrics = MetricsRegistry(
        [
            "readings_generated_total",
            "gateway_send_successes_total",
            "gateway_send_failures_total",
        ]
    )

    def sample_reading() -> TemperatureReading:
        reading = simulator.sample()
        metrics.increment("readings_generated_total")
        return reading

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.latest_reading = sample_reading()
        LOGGER.info(
            "event=sensor_started sensor_id=%s gateway_url=%s",
            resolved.sensor_id,
            resolved.gateway_url,
        )
        forward_task = asyncio.create_task(_forward_readings(application))
        application.state.forward_task = forward_task
        yield
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task
        LOGGER.info("event=sensor_stopped sensor_id=%s", resolved.sensor_id)

    application = FastAPI(
        title="Simulated Temperature Sensor",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.simulator = simulator
    application.state.transport = transport
    application.state.metrics = metrics
    application.state.consecutive_gateway_failures = 0
    application.state.sample_reading = sample_reading

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    def readiness_payload(request: Request) -> tuple[int, dict[str, object]]:
        latest_reading = getattr(request.app.state, "latest_reading", None)
        forward_task = getattr(request.app.state, "forward_task", None)
        checks = {
            "configuration_loaded": True,
            "latest_reading_available": isinstance(latest_reading, TemperatureReading),
            "forwarder_running": forward_task is not None and not forward_task.done(),
        }
        ready = all(checks.values())
        return (
            status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            {
                "status": "ready" if ready else "not_ready",
                "checks": checks,
                "sensor_id": request.app.state.settings.sensor_id,
            },
        )

    @application.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        status_code, payload = readiness_payload(request)
        if status_code != status.HTTP_200_OK:
            LOGGER.warning("event=sensor_readiness_failed checks=%s", payload["checks"])
        return JSONResponse(status_code=status_code, content=payload)

    @application.get("/metrics")
    async def service_metrics(request: Request) -> dict[str, object]:
        payload = request.app.state.metrics.snapshot()
        payload["sensor_id"] = request.app.state.settings.sensor_id
        payload["consecutive_gateway_failures"] = (
            request.app.state.consecutive_gateway_failures
        )
        payload["latest_reading_available"] = hasattr(
            request.app.state,
            "latest_reading",
        )
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
                    "message": "sensor readiness checks are failing",
                }
            )
        if (
            request.app.state.consecutive_gateway_failures
            >= request.app.state.settings.failure_warning_threshold
        ):
            warnings.append(
                {
                    "code": "repeated_gateway_send_failures",
                    "severity": "warning",
                    "message": "sensor has repeated failures sending to the gateway",
                }
            )
        return monitoring_payload(warnings)

    @application.get("/v1/readings/latest", response_model=TemperatureReading)
    async def latest_reading(request: Request) -> TemperatureReading:
        return request.app.state.latest_reading

    return application


app = create_app()
