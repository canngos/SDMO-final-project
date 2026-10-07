import json
import time

import httpx
from fastapi.testclient import TestClient

from temperature_pqc.config import CloudSettings, GatewaySettings, SensorSettings
from temperature_pqc.crypto import LegacyCloudKeyPair, encrypt_for_cloud
from temperature_pqc.models import PublicKeyDocument, TemperatureReading
from temperature_pqc.services.cloud import create_app as create_cloud_app
from temperature_pqc.services.gateway import create_app as create_gateway_app
from temperature_pqc.services.sensor import create_app as create_sensor_app


def _reading(sensor_id: str = "sensor-01") -> TemperatureReading:
    return TemperatureReading(
        sensor_id=sensor_id,
        temperature_c=21.5,
        measured_at="2026-09-26T08:00:00Z",
    )


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_cloud_readiness_metrics_and_monitoring(tmp_path) -> None:
    settings = CloudSettings(
        database_path=str(tmp_path / "readings.db"),
        mlkem_seed_path=str(tmp_path / "mlkem-768.seed"),
    )
    app = create_cloud_app(settings)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json()["status"] == "ready"

        public_document = client.get("/v1/crypto/public-key").json()
        reading = _reading()
        envelope = encrypt_for_cloud(
            PublicKeyDocument.model_validate(public_document),
            reading.message_id,
            reading.model_dump_json().encode("utf-8"),
        )

        response = client.post("/v1/readings", json=envelope.model_dump(mode="json"))

        assert response.status_code == 200
        metrics = client.get("/metrics").json()
        metrics_json = json.dumps(metrics)
        assert metrics["envelopes_received_total"] == 1
        assert metrics["readings_persisted_total"] == 1
        assert metrics["readings_stored_current"] == 1
        assert "temperature_c" not in metrics_json
        assert "21.5" not in metrics_json
        assert client.get("/monitoring").json() == {"status": "ok", "warnings": []}

        app.state.store.ready = lambda: False
        not_ready = client.get("/ready")

    assert not_ready.status_code == 503
    assert not_ready.json()["checks"]["database_accessible"] is False


def test_gateway_readiness_metrics_and_monitoring_show_outbox_pressure(tmp_path) -> None:
    cloud_key = LegacyCloudKeyPair()

    def unavailable_cloud(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=cloud_key.public_document().model_dump())
        return httpx.Response(503)

    settings = GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode="rsa",
        outbox_database_path=str(tmp_path / "gateway-outbox.db"),
        outbox_max_pending=1,
        retry_initial_seconds=60.0,
        retry_max_seconds=60.0,
        outbox_poll_seconds=0.01,
        outbox_warning_threshold=1,
        delivery_failure_warning_threshold=1,
    )
    app = create_gateway_app(settings, transport=httpx.MockTransport(unavailable_cloud))

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: client.get("/metrics").json()["outbox_retry_attempts_total"] == 1)

        health = client.get("/health")
        ready = client.get("/ready")
        metrics = client.get("/metrics").json()
        monitoring = client.get("/monitoring").json()

    assert response.status_code == 202
    assert health.status_code == 200
    assert ready.status_code == 503
    assert metrics["readings_accepted_total"] == 1
    assert metrics["cloud_delivery_failures_total"] == 1
    assert metrics["outbox_pending"] == 1
    assert "temperature_c" not in json.dumps(metrics)
    assert {warning["code"] for warning in monitoring["warnings"]} >= {
        "service_not_ready",
        "outbox_depth_high",
        "repeated_delivery_failures",
    }


def test_sensor_monitoring_warning_clears_after_gateway_recovers() -> None:
    accepting = {"value": False}

    def gateway(request: httpx.Request) -> httpx.Response:
        if accepting["value"]:
            return httpx.Response(202, json={"status": "queued"})
        return httpx.Response(503, json={"detail": "temporarily unavailable"})

    settings = SensorSettings(
        gateway_url="http://gateway.test",
        sensor_id="sensor-01",
        interval_seconds=60.0,
        timeout_seconds=2.0,
        retry_seconds=0.01,
        failure_warning_threshold=1,
    )
    app = create_sensor_app(settings, transport=httpx.MockTransport(gateway))

    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        _wait_until(
            lambda: client.get("/monitoring").json()["status"] == "warning",
        )
        warning = client.get("/monitoring").json()
        accepting["value"] = True
        _wait_until(lambda: client.get("/metrics").json()["gateway_send_successes_total"] >= 1)
        recovered = client.get("/monitoring").json()
        metrics = client.get("/metrics").json()

    assert warning["warnings"][0]["code"] == "repeated_gateway_send_failures"
    assert recovered == {"status": "ok", "warnings": []}
    assert metrics["readings_generated_total"] >= 1
    assert "temperature_c" not in json.dumps(metrics)
