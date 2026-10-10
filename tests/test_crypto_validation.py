import base64
import hashlib
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from temperature_pqc import crypto
from temperature_pqc.crypto import (
    LegacyCloudKeyPair,
    MlKemCloudKeyPair,
    MlKemPublicKeyError,
    encrypt_for_cloud,
    encrypt_for_cloud_mlkem,
)


@pytest.fixture
def mlkem_pair(tmp_path):
    return MlKemCloudKeyPair.load_or_create(str(tmp_path / 'mlkem.seed'))


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('key_id', '0' * 16, 'unknown key_id'),
    ('nonce', base64.b64encode(b'short').decode(), 'invalid nonce'),
])
def test_rsa_rejects_wrong_key_or_nonce(field, value, message):
    pair = LegacyCloudKeyPair()
    envelope = encrypt_for_cloud(pair.public_document(), uuid4(), b'reading')
    with pytest.raises(ValueError, match=message):
        pair.decrypt(envelope.model_copy(update={field: value}))


def test_rsa_rejects_non_rsa_public_key():
    pair = LegacyCloudKeyPair()
    public_pem = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('ascii')
    document = pair.public_document().model_copy(update={'public_key_pem': public_pem})
    with pytest.raises(TypeError, match='not RSA'):
        encrypt_for_cloud(document, uuid4(), b'reading')


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('key_id', '0' * 64, 'unknown key_id'),
    ('kem_ciphertext', base64.b64encode(b'short').decode(), 'invalid ML-KEM ciphertext'),
    ('nonce', base64.b64encode(b'short').decode(), 'invalid nonce'),
])
def test_mlkem_rejects_wrong_key_or_lengths(mlkem_pair, field, value, message):
    envelope = encrypt_for_cloud_mlkem(
        mlkem_pair.public_document(), mlkem_pair.key_id, uuid4(), b'reading'
    )
    with pytest.raises(ValueError, match=message):
        mlkem_pair.decrypt(envelope.model_copy(update={field: value}))


@pytest.mark.parametrize('field', ['kem_ciphertext', 'ciphertext', 'message_id', 'nonce'])
def test_mlkem_authentication_rejects_modified_envelope(mlkem_pair, field):
    envelope = encrypt_for_cloud_mlkem(
        mlkem_pair.public_document(), mlkem_pair.key_id, uuid4(), b'reading'
    )
    if field == 'message_id':
        changed = uuid4()
    else:
        data = bytearray(base64.b64decode(getattr(envelope, field)))
        data[-1] ^= 1
        changed = base64.b64encode(data).decode()
    with pytest.raises(InvalidTag):
        mlkem_pair.decrypt(envelope.model_copy(update={field: changed}))


def test_mlkem_rejects_invalid_base64_before_encapsulation(mlkem_pair):
    document = mlkem_pair.public_document().model_copy(update={'public_key_b64': '!!!!'})
    with pytest.raises(MlKemPublicKeyError, match='encoding'):
        encrypt_for_cloud_mlkem(document, mlkem_pair.key_id, uuid4(), b'reading')


def test_mlkem_rejects_malformed_key_even_with_matching_fingerprint(mlkem_pair):
    key = b'too short for ML-KEM'
    fingerprint = hashlib.sha256(key).hexdigest()
    document = mlkem_pair.public_document().model_copy(update={
        'key_id': fingerprint, 'public_key_b64': base64.b64encode(key).decode(),
    })
    with pytest.raises(MlKemPublicKeyError, match='invalid ML-KEM public key'):
        encrypt_for_cloud_mlkem(document, fingerprint, uuid4(), b'reading')


def test_mlkem_backend_failure_is_reported_without_fallback(mlkem_pair, monkeypatch):
    class UnsupportedPublicKey:
        @staticmethod
        def from_public_bytes(data):
            raise UnsupportedAlgorithm('backend unavailable')

    monkeypatch.setattr(crypto, 'MLKEM768PublicKey', UnsupportedPublicKey)
    with pytest.raises(MlKemPublicKeyError, match='invalid ML-KEM public key'):
        encrypt_for_cloud_mlkem(
            mlkem_pair.public_document(), mlkem_pair.key_id, uuid4(), b'reading'
        )


def test_mlkem_rejects_corrupt_persisted_seed(tmp_path):
    seed_path = tmp_path / 'invalid.seed'
    seed_path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='invalid ML-KEM-768 seed file'):
        MlKemCloudKeyPair.load_or_create(str(seed_path))
    assert seed_path.read_bytes() == b'corrupt'


def test_concurrent_seed_creator_reuses_winning_key(tmp_path, monkeypatch):
    winning_key = crypto.MLKEM768PrivateKey.generate()
    seed = winning_key.private_bytes_raw()
    path = tmp_path / 'race.seed'

    def competing_create(*args):
        path.write_bytes(seed)
        raise FileExistsError('another process created the seed')

    monkeypatch.setattr(crypto.os, 'open', competing_create)
    pair = MlKemCloudKeyPair.load_or_create(str(path))
    assert pair.key_id == MlKemCloudKeyPair(winning_key).key_id
    assert path.read_bytes() == seed
