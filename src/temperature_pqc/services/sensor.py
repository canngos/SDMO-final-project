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
from fastapi import FastAPI, Request

from temperature_pqc.config import SensorSettings
from temperature_pqc.models import TemperatureReading

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.sensor")


class TemperatureSimulator:
    def __init__(self, sensor_id: str, initial_temperature: float = 21.0) -> None:
        self.sensor_id = sensor_id
        self.temperature = initial_temperature

    def sample(self) -> TemperatureReading:
        drifted = self.temperature + random.gauss(0.0, 0.25)
        self.temperature = round(min(35.0, max(10.0, drifted)), 2)
        return TemperatureReading(
            sensor_id=self.sensor_id,
            temperature_c=self.temperature,
            measured_at=datetime.now(UTC),
        )


async def _forward_readings(application: FastAPI) -> None:
    settings: SensorSettings = application.state.settings
    async with httpx.AsyncClient(
        transport=application.state.transport,
        timeout=settings.timeout_seconds,
    ) as client:
        while True:
            reading: TemperatureReading = application.state.latest_reading
            try:
                response = await client.post(
                    f"{settings.gateway_url}/v1/readings",
                    json=reading.model_dump(mode="json"),
                )
                response.raise_for_status()
                LOGGER.info("sent reading %s", reading.message_id)
            except httpx.HTTPError:
                LOGGER.exception("failed to send reading %s", reading.message_id)

            await asyncio.sleep(settings.interval_seconds)
            application.state.latest_reading = application.state.simulator.sample()


def create_app(
    settings: SensorSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    resolved = settings or SensorSettings.from_env()
    simulator = TemperatureSimulator(resolved.sensor_id)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.latest_reading = simulator.sample()
        forward_task = asyncio.create_task(_forward_readings(application))
        yield
        forward_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward_task

    application = FastAPI(
        title="Simulated Temperature Sensor",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.simulator = simulator
    application.state.transport = transport

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/readings/latest", response_model=TemperatureReading)
    async def latest_reading(request: Request) -> TemperatureReading:
        return request.app.state.latest_reading

    return application


app = create_app()
