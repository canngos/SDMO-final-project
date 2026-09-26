import json
import time

import httpx
from fastapi.testclient import TestClient

from temperature_pqc.config import SensorSettings
from temperature_pqc.services.sensor import create_app


def test_sensor_retries_the_same_message_id_until_gateway_accepts() -> None:
    received_message_ids: list[str] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        received_message_ids.append(json.loads(request.content)["message_id"])
        if len(received_message_ids) == 1:
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(202, json={"status": "queued"})

    settings = SensorSettings(
        gateway_url="http://gateway.test",
        sensor_id="sensor-01",
        interval_seconds=60.0,
        timeout_seconds=2.0,
        retry_seconds=0.01,
    )
    app = create_app(settings, transport=httpx.MockTransport(gateway))

    with TestClient(app):
        deadline = time.monotonic() + 2.0
        while len(received_message_ids) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert len(received_message_ids) >= 2
    assert received_message_ids[0] == received_message_ids[1]
