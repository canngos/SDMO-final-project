import json
import time

import httpx
from fastapi.testclient import TestClient

from temperature_pqc.config import GatewaySettings
from temperature_pqc.crypto import LegacyCloudKeyPair, MlKemCloudKeyPair
from temperature_pqc.models import (
    MlKemEncryptedEnvelope,
    RsaEncryptedEnvelope,
    TemperatureReading,
)
from temperature_pqc.services.gateway import create_app


def _reading() -> TemperatureReading:
    return TemperatureReading(
        sensor_id="sensor-01",
        temperature_c=21.75,
        measured_at="2026-09-25T12:00:00Z",
    )


def _settings(tmp_path, *, mode: str, expected_key_id: str | None = None) -> GatewaySettings:
    return GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode=mode,
        expected_mlkem_key_id=expected_key_id,
        outbox_database_path=str(tmp_path / "gateway-outbox.db"),
        retry_initial_seconds=60.0,
        retry_max_seconds=60.0,
        outbox_poll_seconds=0.01,
    )


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_gateway_sends_mlkem_envelope_with_pinned_key(tmp_path) -> None:
    cloud_key = MlKemCloudKeyPair.load_or_create(str(tmp_path / "mlkem.seed"))
    requested_paths: list[str] = []

    def cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.method == "GET" and request.url.path == "/v2/crypto/public-key":
            return httpx.Response(200, json=cloud_key.public_document().model_dump())
        if request.method == "POST" and request.url.path == "/v1/readings":
            envelope = MlKemEncryptedEnvelope.model_validate(json.loads(request.content))
            plaintext = cloud_key.decrypt(envelope)
            decrypted = TemperatureReading.model_validate_json(plaintext)
            return httpx.Response(
                200,
                json={
                    "status": "stored",
                    "message_id": str(decrypted.message_id),
                    "security_mode": "mlkem",
                },
            )
        return httpx.Response(404)

    app = create_app(
        _settings(tmp_path, mode="mlkem", expected_key_id=cloud_key.key_id),
        transport=httpx.MockTransport(cloud),
    )

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: requested_paths.count("/v1/readings") == 1)
        _wait_until(lambda: client.get("/v1/outbox/status").json()["pending"] == 0)

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert requested_paths == ["/v2/crypto/public-key", "/v1/readings"]


def test_gateway_preserves_explicit_rsa_mode(tmp_path) -> None:
    cloud_key = LegacyCloudKeyPair()
    requested_paths: list[str] = []

    def cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.method == "GET" and request.url.path == "/v1/crypto/public-key":
            return httpx.Response(200, json=cloud_key.public_document().model_dump())
        if request.method == "POST" and request.url.path == "/v1/readings":
            envelope = RsaEncryptedEnvelope.model_validate(json.loads(request.content))
            cloud_key.decrypt(envelope)
            return httpx.Response(200, json={"status": "stored"})
        return httpx.Response(404)

    app = create_app(
        _settings(tmp_path, mode="rsa"),
        transport=httpx.MockTransport(cloud),
    )

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: requested_paths.count("/v1/readings") == 1)
        _wait_until(lambda: client.get("/v1/outbox/status").json()["pending"] == 0)

    assert response.status_code == 202
    assert requested_paths == ["/v1/crypto/public-key", "/v1/readings"]


def test_key_pin_mismatch_stays_queued_without_rsa_fallback(tmp_path) -> None:
    cloud_key = MlKemCloudKeyPair.load_or_create(str(tmp_path / "mlkem.seed"))
    requested_paths: list[str] = []

    def cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json=cloud_key.public_document().model_dump())

    app = create_app(
        _settings(tmp_path, mode="mlkem", expected_key_id="0" * 64),
        transport=httpx.MockTransport(cloud),
    )

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: len(requested_paths) == 1)
        _wait_until(lambda: client.get("/health").status_code == 503)
        outbox_status = client.get("/v1/outbox/status").json()

    assert response.status_code == 202
    assert outbox_status["pending"] == 1
    assert outbox_status["total_retries"] == 1
    assert requested_paths == ["/v2/crypto/public-key"]


def test_public_key_fingerprint_is_recalculated_before_use(tmp_path) -> None:
    pinned_key = MlKemCloudKeyPair.load_or_create(str(tmp_path / "pinned.seed"))
    substituted_key = MlKemCloudKeyPair.load_or_create(str(tmp_path / "substituted.seed"))
    substituted_document = substituted_key.public_document().model_copy(
        update={"key_id": pinned_key.key_id}
    )
    requested_paths: list[str] = []

    def cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json=substituted_document.model_dump())

    app = create_app(
        _settings(tmp_path, mode="mlkem", expected_key_id=pinned_key.key_id),
        transport=httpx.MockTransport(cloud),
    )

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: len(requested_paths) == 1)
        outbox_status = client.get("/v1/outbox/status").json()

    assert response.status_code == 202
    assert outbox_status["pending"] == 1
    assert requested_paths == ["/v2/crypto/public-key"]


def test_network_failure_is_queued_without_rsa_fallback(tmp_path) -> None:
    requested_paths: list[str] = []

    def unavailable_cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(503)

    app = create_app(
        _settings(tmp_path, mode="mlkem", expected_key_id="0" * 64),
        transport=httpx.MockTransport(unavailable_cloud),
    )

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: len(requested_paths) == 1)
        health = client.get("/health")
        outbox_status = client.get("/v1/outbox/status").json()

    assert response.status_code == 202
    assert health.status_code == 200
    assert outbox_status["pending"] == 1
    assert requested_paths == ["/v2/crypto/public-key"]


def test_cloud_duplicate_is_treated_as_delivered(tmp_path) -> None:
    cloud_key = LegacyCloudKeyPair()

    def duplicate_cloud(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=cloud_key.public_document().model_dump())
        return httpx.Response(409, json={"detail": "duplicate message_id"})

    app = create_app(
        _settings(tmp_path, mode="rsa"),
        transport=httpx.MockTransport(duplicate_cloud),
    )

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        _wait_until(lambda: client.get("/v1/outbox/status").json()["pending"] == 0)

    assert response.status_code == 202


def test_pending_rsa_reading_drains_with_mlkem_after_gateway_restart(tmp_path) -> None:
    rsa_key = LegacyCloudKeyPair()
    reading = _reading()

    def rsa_rejected(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=rsa_key.public_document().model_dump())
        return httpx.Response(403, json={"detail": "RSA ingestion is disabled"})

    rsa_app = create_app(
        _settings(tmp_path, mode="rsa"),
        transport=httpx.MockTransport(rsa_rejected),
    )
    with TestClient(rsa_app) as client:
        response = client.post("/v1/readings", json=reading.model_dump(mode="json"))
        _wait_until(
            lambda: client.get("/v1/outbox/status").json()["total_retries"] == 1
        )
        assert client.get("/v1/outbox/status").json()["pending"] == 1

    mlkem_key = MlKemCloudKeyPair.load_or_create(str(tmp_path / "mlkem.seed"))
    delivered_message_ids: list[str] = []

    def mlkem_cloud(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=mlkem_key.public_document().model_dump())
        envelope = MlKemEncryptedEnvelope.model_validate(json.loads(request.content))
        plaintext = mlkem_key.decrypt(envelope)
        delivered = TemperatureReading.model_validate_json(plaintext)
        delivered_message_ids.append(str(delivered.message_id))
        return httpx.Response(200, json={"status": "stored"})

    mlkem_app = create_app(
        _settings(tmp_path, mode="mlkem", expected_key_id=mlkem_key.key_id),
        transport=httpx.MockTransport(mlkem_cloud),
    )
    with TestClient(mlkem_app) as client:
        _wait_until(lambda: client.get("/v1/outbox/status").json()["pending"] == 0)

    assert delivered_message_ids == [str(reading.message_id)]


def test_mlkem_mode_requires_a_valid_pinned_key_id() -> None:
    try:
        GatewaySettings(
            cloud_url="http://cloud.test",
            timeout_seconds=2.0,
            crypto_mode="mlkem",
        )
    except ValueError as exc:
        assert "EXPECTED_MLKEM_KEY_ID is required" in str(exc)
    else:
        raise AssertionError("ML-KEM mode accepted a missing key pin")
