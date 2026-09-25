"""Classical hybrid encryption used by the intentionally legacy baseline."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PrivateKey
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from temperature_pqc.models import (
    MLKEM_ALGORITHM,
    EncryptedEnvelope,
    MlKemEncryptedEnvelope,
    MlKemPublicKeyDocument,
    PublicKeyDocument,
)

LOGGER = logging.getLogger("temperature_pqc.crypto")


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def mlkem_aad(*, key_id: str, message_id: UUID) -> bytes:
    return json.dumps(
        {
            "alg": MLKEM_ALGORITHM,
            "key_id": key_id,
            "message_id": str(message_id),
            "version": 2,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def derive_mlkem_aes_key(*, shared_secret: bytes, key_id: str, message_id: UUID) -> bytes:
    info = b"temperature-pqc/gateway-cloud/v2|" + mlkem_aad(
        key_id=key_id,
        message_id=message_id,
    )
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(shared_secret)


class LegacyCloudKeyPair:
    """RSA key transport is functional but not secure against quantum attacks."""

    def __init__(self, private_key: rsa.RSAPrivateKey | None = None) -> None:
        self._private_key = private_key or rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self._public_pem = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.key_id = hashlib.sha256(self._public_pem).hexdigest()[:16]
        LOGGER.info("event=rsa_key_ready key_id=%s", self.key_id)

    def public_document(self) -> PublicKeyDocument:
        return PublicKeyDocument(
            key_id=self.key_id,
            public_key_pem=self._public_pem.decode("ascii"),
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> bytes:
        if envelope.key_id != self.key_id:
            raise ValueError("unknown key_id")
        aes_key = self._private_key.decrypt(
            _b64decode(envelope.wrapped_key),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        nonce = _b64decode(envelope.nonce)
        if len(nonce) != 12:
            raise ValueError("invalid nonce")
        plaintext = AESGCM(aes_key).decrypt(
            nonce,
            _b64decode(envelope.ciphertext),
            str(envelope.message_id).encode("ascii"),
        )
        LOGGER.info(
            "event=rsa_payload_decrypted message_id=%s key_id=%s",
            envelope.message_id,
            envelope.key_id,
        )
        return plaintext


class MlKemCloudKeyPair:
    """Persistent ML-KEM-768 key used by the dual-capable cloud."""

    def __init__(self, private_key: MLKEM768PrivateKey) -> None:
        self._private_key = private_key
        self._public_bytes = private_key.public_key().public_bytes_raw()
        self.key_id = hashlib.sha256(self._public_bytes).hexdigest()

    @classmethod
    def load_or_create(cls, seed_path: str) -> MlKemCloudKeyPair:
        path = Path(seed_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            seed = path.read_bytes()
            LOGGER.info("event=mlkem_seed_loaded path=%s", path)
        except FileNotFoundError:
            generated = MLKEM768PrivateKey.generate()
            seed = generated.private_bytes_raw()
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                seed = path.read_bytes()
                LOGGER.info("event=mlkem_seed_loaded path=%s", path)
            else:
                with os.fdopen(descriptor, "wb") as seed_file:
                    seed_file.write(seed)
                    seed_file.flush()
                    os.fsync(seed_file.fileno())
                LOGGER.info("event=mlkem_seed_generated path=%s", path)

        if len(seed) != 64:
            raise ValueError(f"invalid ML-KEM-768 seed file: {path}")
        key_pair = cls(MLKEM768PrivateKey.from_seed_bytes(seed))
        LOGGER.info("event=mlkem_key_ready key_id=%s", key_pair.key_id)
        return key_pair

    def public_document(self) -> MlKemPublicKeyDocument:
        return MlKemPublicKeyDocument(
            key_id=self.key_id,
            public_key_b64=_b64encode(self._public_bytes),
        )

    def decrypt(self, envelope: MlKemEncryptedEnvelope) -> bytes:
        if envelope.key_id != self.key_id:
            raise ValueError("unknown key_id")
        kem_ciphertext = _b64decode(envelope.kem_ciphertext)
        if len(kem_ciphertext) != 1088:
            raise ValueError("invalid ML-KEM ciphertext")
        nonce = _b64decode(envelope.nonce)
        if len(nonce) != 12:
            raise ValueError("invalid nonce")
        shared_secret = self._private_key.decapsulate(kem_ciphertext)
        aes_key = derive_mlkem_aes_key(
            shared_secret=shared_secret,
            key_id=envelope.key_id,
            message_id=envelope.message_id,
        )
        plaintext = AESGCM(aes_key).decrypt(
            nonce,
            _b64decode(envelope.ciphertext),
            mlkem_aad(key_id=envelope.key_id, message_id=envelope.message_id),
        )
        LOGGER.info(
            "event=mlkem_payload_decrypted message_id=%s key_id=%s",
            envelope.message_id,
            envelope.key_id,
        )
        return plaintext


def encrypt_for_cloud(
    public_document: PublicKeyDocument,
    message_id: UUID,
    plaintext: bytes,
) -> EncryptedEnvelope:
    public_key = serialization.load_pem_public_key(public_document.public_key_pem.encode("ascii"))
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("cloud key is not RSA")
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    wrapped_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    ciphertext = AESGCM(aes_key).encrypt(
        nonce,
        plaintext,
        str(message_id).encode("ascii"),
    )
    envelope = EncryptedEnvelope(
        key_id=public_document.key_id,
        message_id=message_id,
        wrapped_key=_b64encode(wrapped_key),
        nonce=_b64encode(nonce),
        ciphertext=_b64encode(ciphertext),
    )
    LOGGER.info(
        "event=rsa_envelope_created message_id=%s key_id=%s",
        message_id,
        public_document.key_id,
    )
    return envelope
