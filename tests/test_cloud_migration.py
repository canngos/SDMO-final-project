import base64
import os

from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from temperature_pqc.config import CloudSettings
from temperature_pqc.crypto import derive_mlkem_aes_key, encrypt_for_cloud, mlkem_aad
from temperature_pqc.models import (
    MlKemEncryptedEnvelope,
    MlKemPublicKeyDocument,
    PublicKeyDocument,
    TemperatureReading,
)
from temperature_pqc.services.cloud import create_app


def _settings(tmp_path, *, allow_rsa_ingest: bool = True) -> CloudSettings:
    return CloudSettings(
        database_path=str(tmp_path / "readings.db"),
        mlkem_seed_path=str(tmp_path / "mlkem-768.seed"),
        allow_rsa_ingest=allow_rsa_ingest,
    )


def _mlkem_envelope(
    document: MlKemPublicKeyDocument,
    reading: TemperatureReading,
) -> MlKemEncryptedEnvelope:
    public_key = MLKEM768PublicKey.from_public_bytes(
        base64.b64decode(document.public_key_b64, validate=True)
    )
    shared_secret, kem_ciphertext = public_key.encapsulate()
    aes_key = derive_mlkem_aes_key(
        shared_secret=shared_secret,
        key_id=document.key_id,
        message_id=reading.message_id,
    )
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(
        nonce,
        reading.model_dump_json().encode("utf-8"),
        mlkem_aad(key_id=document.key_id, message_id=reading.message_id),
    )
    return MlKemEncryptedEnvelope(
        key_id=document.key_id,
        message_id=reading.message_id,
        kem_ciphertext=base64.b64encode(kem_ciphertext).decode("ascii"),
        nonce=base64.b64encode(nonce).decode("ascii"),
        ciphertext=base64.b64encode(ciphertext).decode("ascii"),
    )


def test_existing_rsa_gateway_payload_is_still_accepted(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        public_document = PublicKeyDocument.model_validate(
            client.get("/v1/crypto/public-key").json()
        )
        reading = TemperatureReading(
            sensor_id="sensor-01",
            temperature_c=21.5,
            measured_at="2026-09-25T10:00:00Z",
        )
        envelope = encrypt_for_cloud(
            public_document,
            reading.message_id,
            reading.model_dump_json().encode("utf-8"),
        )

        response = client.post("/v1/readings", json=envelope.model_dump(mode="json"))

        assert response.status_code == 200
        assert response.json()["security_mode"] == "rsa"


def test_cloud_accepts_mlkem_envelope_and_records_security_mode(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        document = MlKemPublicKeyDocument.model_validate(
            client.get("/v2/crypto/public-key").json()
        )
        reading = TemperatureReading(
            sensor_id="sensor-01",
            temperature_c=22.25,
            measured_at="2026-09-25T10:01:00Z",
        )
        envelope = _mlkem_envelope(document, reading)

        response = client.post("/v1/readings", json=envelope.model_dump(mode="json"))

        assert response.status_code == 200
        assert response.json()["security_mode"] == "mlkem"
        stored = client.get("/v1/readings").json()
        assert stored[0]["message_id"] == str(reading.message_id)
        assert stored[0]["security_mode"] == "mlkem"


def test_tampered_mlkem_payload_is_rejected(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        document = MlKemPublicKeyDocument.model_validate(
            client.get("/v2/crypto/public-key").json()
        )
        reading = TemperatureReading(
            sensor_id="sensor-01",
            temperature_c=23.0,
            measured_at="2026-09-25T10:02:00Z",
        )
        envelope = _mlkem_envelope(document, reading)
        ciphertext = bytearray(base64.b64decode(envelope.ciphertext, validate=True))
        ciphertext[-1] ^= 1
        tampered = envelope.model_copy(
            update={"ciphertext": base64.b64encode(ciphertext).decode("ascii")}
        )

        response = client.post("/v1/readings", json=tampered.model_dump(mode="json"))

        assert response.status_code == 400
        assert response.json() == {"detail": "invalid encrypted reading"}


def test_mlkem_key_is_stable_across_cloud_restarts(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        first = client.get("/v2/crypto/public-key").json()
    with TestClient(create_app(settings)) as client:
        second = client.get("/v2/crypto/public-key").json()

    assert first == second
    assert (tmp_path / "mlkem-768.seed").stat().st_size == 64


def test_rsa_is_rejected_when_migration_flag_is_disabled(tmp_path) -> None:
    app = create_app(_settings(tmp_path, allow_rsa_ingest=False))
    with TestClient(app) as client:
        key_response = client.get("/v1/crypto/public-key")
        response = client.post(
            "/v1/readings",
            json={
                "version": 1,
                "alg": "RSA-OAEP-2048+AES-256-GCM",
                "key_id": "0" * 16,
                "message_id": "2bdabf75-fc62-470a-a182-88f78908d271",
                "wrapped_key": base64.b64encode(b"not-a-real-key").decode("ascii"),
                "nonce": base64.b64encode(b"0" * 12).decode("ascii"),
                "ciphertext": base64.b64encode(b"not-a-real-ciphertext").decode("ascii"),
            },
        )

    assert key_response.status_code == 403
    assert response.status_code == 403


def test_mixed_or_unknown_algorithm_envelope_is_rejected(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/v1/readings",
            json={
                "version": 2,
                "alg": "ML-KEM-768+AES-256-GCM",
                "key_id": "0" * 64,
                "message_id": "2bdabf75-fc62-470a-a182-88f78908d271",
                "wrapped_key": base64.b64encode(b"mixed-field").decode("ascii"),
                "nonce": base64.b64encode(b"0" * 12).decode("ascii"),
                "ciphertext": base64.b64encode(b"not-a-real-ciphertext").decode("ascii"),
            },
        )

    assert response.status_code == 422
