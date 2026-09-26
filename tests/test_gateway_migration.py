import json

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

    settings = GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode="mlkem",
        expected_mlkem_key_id=cloud_key.key_id,
    )
    app = create_app(settings, transport=httpx.MockTransport(cloud))

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))

    assert response.status_code == 202
    assert response.json()["security_mode"] == "mlkem"
    assert requested_paths == ["/v2/crypto/public-key", "/v1/readings"]


def test_gateway_preserves_explicit_rsa_mode() -> None:
    cloud_key = LegacyCloudKeyPair()
    requested_paths: list[str] = []

    def cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.method == "GET" and request.url.path == "/v1/crypto/public-key":
            return httpx.Response(200, json=cloud_key.public_document().model_dump())
        if request.method == "POST" and request.url.path == "/v1/readings":
            envelope = RsaEncryptedEnvelope.model_validate(json.loads(request.content))
            plaintext = cloud_key.decrypt(envelope)
            decrypted = TemperatureReading.model_validate_json(plaintext)
            return httpx.Response(
                200,
                json={
                    "status": "stored",
                    "message_id": str(decrypted.message_id),
                    "security_mode": "rsa",
                },
            )
        return httpx.Response(404)

    settings = GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode="rsa",
    )
    app = create_app(settings, transport=httpx.MockTransport(cloud))

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))

    assert response.status_code == 202
    assert response.json()["security_mode"] == "rsa"
    assert requested_paths == ["/v1/crypto/public-key", "/v1/readings"]


def test_key_pin_mismatch_fails_closed_without_rsa_fallback(tmp_path) -> None:
    cloud_key = MlKemCloudKeyPair.load_or_create(str(tmp_path / "mlkem.seed"))
    requested_paths: list[str] = []

    def cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json=cloud_key.public_document().model_dump())

    settings = GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode="mlkem",
        expected_mlkem_key_id="0" * 64,
    )
    app = create_app(settings, transport=httpx.MockTransport(cloud))

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        health = client.get("/health")

    assert response.status_code == 502
    assert response.json()["detail"] == "cloud ML-KEM key validation failed"
    assert health.status_code == 503
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

    settings = GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode="mlkem",
        expected_mlkem_key_id=pinned_key.key_id,
    )
    app = create_app(settings, transport=httpx.MockTransport(cloud))

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))

    assert response.status_code == 502
    assert response.json()["detail"] == "cloud ML-KEM key validation failed"
    assert requested_paths == ["/v2/crypto/public-key"]


def test_mlkem_network_failure_does_not_request_rsa_fallback() -> None:
    requested_paths: list[str] = []

    def unavailable_cloud(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(503)

    settings = GatewaySettings(
        cloud_url="http://cloud.test",
        timeout_seconds=2.0,
        crypto_mode="mlkem",
        expected_mlkem_key_id="0" * 64,
    )
    app = create_app(settings, transport=httpx.MockTransport(unavailable_cloud))

    with TestClient(app) as client:
        response = client.post("/v1/readings", json=_reading().model_dump(mode="json"))
        health = client.get("/health")

    assert response.status_code == 502
    assert health.status_code == 200
    assert requested_paths == ["/v2/crypto/public-key"]


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
