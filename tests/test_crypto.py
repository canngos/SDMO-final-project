from uuid import uuid4

from temperature_pqc.crypto import LegacyCloudKeyPair, encrypt_for_cloud


def test_rsa_hybrid_round_trip() -> None:
    key_pair = LegacyCloudKeyPair()
    message_id = uuid4()
    plaintext = b'{"temperature_c":21.5}'

    envelope = encrypt_for_cloud(key_pair.public_document(), message_id, plaintext)

    assert key_pair.decrypt(envelope) == plaintext
    assert envelope.message_id == message_id
    assert envelope.ciphertext != plaintext.decode()
