"""Simulated constrained temperature sensor."""

from __future__ import annotations

import argparse
import logging
import os
import random
import time
from datetime import UTC, datetime

import httpx

from temperature_pqc.models import TemperatureReading

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOGGER = logging.getLogger("temperature_pqc.sensor")


def generate_temperature(previous: float) -> float:
    drifted = previous + random.gauss(0.0, 0.25)
    return round(min(35.0, max(10.0, drifted)), 2)


def run(gateway_url: str, sensor_id: str, interval: float, count: int) -> None:
    temperature = 21.0
    sent = 0
    with httpx.Client(timeout=5.0) as client:
        while count == 0 or sent < count:
            temperature = generate_temperature(temperature)
            reading = TemperatureReading(
                sensor_id=sensor_id,
                temperature_c=temperature,
                measured_at=datetime.now(UTC),
            )
            try:
                response = client.post(
                    f"{gateway_url.rstrip('/')}/v1/readings",
                    json=reading.model_dump(mode="json"),
                )
                response.raise_for_status()
                LOGGER.info(
                    "reading_sent",
                    extra={"message_id": str(reading.message_id), "sensor_id": sensor_id},
                )
            except httpx.HTTPError:
                LOGGER.exception(
                    "reading_send_failed",
                    extra={"message_id": str(reading.message_id), "sensor_id": sensor_id},
                )
            sent += 1
            if count == 0 or sent < count:
                time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send simulated temperature readings")
    parser.add_argument("--gateway-url", default=os.getenv("GATEWAY_URL", "http://localhost:8001"))
    parser.add_argument("--sensor-id", default=os.getenv("SENSOR_ID", "sensor-01"))
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--count", type=int, default=0, help="0 sends forever")
    args = parser.parse_args()
    if args.interval <= 0 or args.count < 0:
        parser.error("interval must be positive and count must be non-negative")
    run(args.gateway_url, args.sensor_id, args.interval, args.count)


if __name__ == "__main__":
    main()
