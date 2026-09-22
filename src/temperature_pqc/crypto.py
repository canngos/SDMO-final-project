"""Classical hybrid encryption used by the intentionally legacy baseline."""

from __future__ import annotations

import base64
import hashlib
import os
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from temperature_pqc.models import EncryptedEnvelope, PublicKeyDocument


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


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
        return AESGCM(aes_key).decrypt(
            nonce,
            _b64decode(envelope.ciphertext),
            str(envelope.message_id).encode("ascii"),
        )


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
    return EncryptedEnvelope(
        key_id=public_document.key_id,
        message_id=message_id,
        wrapped_key=_b64encode(wrapped_key),
        nonce=_b64encode(nonce),
        ciphertext=_b64encode(ciphertext),
    )

