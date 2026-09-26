# ML-KEM Migration — Phase 1 Summary

## Phase objective

Phase 1 prepared the cloud service for a staged migration from the legacy RSA-2048 key-transport mechanism to ML-KEM-768. The cloud is now capable of processing both protocols, while the gateway intentionally continues to use RSA. This separation allowed the cloud upgrade to be tested without changing the complete system at once.

## System state after Phase 1

- The simulated sensor continues generating and forwarding temperature readings.
- The gateway continues using the original RSA-OAEP and AES-256-GCM protocol.
- The cloud accepts the existing version 1 RSA envelope.
- The cloud also accepts the new version 2 ML-KEM envelope.
- Stored readings record whether `rsa` or `mlkem` protected the gateway-to-cloud transmission.
- RSA remains enabled temporarily through an explicit migration setting.
- No automatic fallback or algorithm negotiation has been introduced.

## Cloud changes

### ML-KEM support

The project dependency was updated to `cryptography>=48.0.1,<49`, providing the ML-KEM-768 implementation used by the cloud.

The cloud now:

1. Loads an existing ML-KEM-768 private seed or generates one during the first startup.
2. Stores the 64-byte seed in the persistent cloud data volume.
3. Creates the seed file with owner-only `0600` permissions in the container.
4. Derives a SHA-256 fingerprint of the ML-KEM public key as its `key_id`.
5. Publishes the ML-KEM public key through `GET /v2/crypto/public-key`.
6. Decapsulates ML-KEM ciphertexts and derives an AES-256 key using HKDF-SHA-256.
7. Authenticates and decrypts the reading using AES-256-GCM.

The private seed, ML-KEM shared secret, derived AES key, and plaintext payload are never written to application logs.

### Versioned protocol models

The two accepted formats are separated by strict models.

The legacy version 1 envelope uses:

- `alg = RSA-OAEP-2048+AES-256-GCM`
- `wrapped_key`
- `nonce`
- `ciphertext`

The new version 2 envelope uses:

- `alg = ML-KEM-768+HKDF-SHA256+AES-256-GCM`
- `kem_ciphertext`
- `nonce`
- `ciphertext`

The version 2 AES-GCM associated data binds the protocol version, algorithm, key identifier, and message identifier. Modification of these fields causes authentication to fail. Unknown algorithms, unexpected fields, invalid Base64 values, incorrect cryptographic lengths, and mixed RSA/ML-KEM structures are rejected.

### Migration control

The `ALLOW_RSA_INGEST` cloud setting controls whether legacy traffic is permitted:

- `true`: the cloud accepts RSA and ML-KEM envelopes.
- `false`: the RSA public-key endpoint and RSA reading ingestion return HTTP 403.

The setting defaults to `true` during the migration. It provides an explicit enforcement switch for a later phase. A failed ML-KEM operation never causes the cloud or gateway to retry automatically with RSA.

### Persistent configuration

The following settings were added:

```text
MLKEM_SEED_PATH=data/mlkem-768.seed
ALLOW_RSA_INGEST=true
```

Docker Compose uses `/app/data/mlkem-768.seed`, which is stored in the existing `cloud-data` volume.

## Diagnostic logging

Searchable `event=<name>` log messages were added across the system. The same `message_id` can be followed through sensor generation, gateway receipt, encryption, cloud validation, and database persistence.

Important events include:

- `reading_sampled`
- `sensor_forward_attempt`
- `gateway_reading_received`
- `cloud_key_received`
- `cloud_envelope_received`
- `cloud_crypto_selected`
- `rsa_payload_decrypted` or `mlkem_payload_decrypted`
- `cloud_payload_validated`
- `reading_persisted`
- `cloud_forward_succeeded`

Logs include operational metadata such as the algorithm, public key identifier, security mode, HTTP status, and error category. They exclude secret cryptographic material and plaintext readings.

The live flow can be followed with:

```bash
docker compose logs --follow sensor gateway cloud
```

## Verification performed

The Phase 1 test suite contains eight passing tests. The migration-specific coverage verifies:

- Existing RSA gateway payloads remain compatible with the upgraded cloud.
- A valid ML-KEM envelope can be decrypted and stored.
- ML-KEM readings are stored with `security_mode="mlkem"`.
- The ML-KEM public key remains unchanged after a cloud restart.
- RSA key publication and ingestion are rejected when RSA is disabled.
- Unknown or mixed algorithm envelopes are rejected.
- Tampered ML-KEM ciphertext is rejected with a generic error response.
- Existing RSA cryptographic and temperature validation tests continue to pass.

Docker verification also confirmed that:

- The cloud image builds with `cryptography` version 48.0.1.
- Cloud, gateway, and sensor services become healthy.
- The ML-KEM seed is 64 bytes and has `0600` permissions in the cloud container.
- The ML-KEM `key_id` remains identical after restarting the cloud container.
- The unchanged RSA gateway continues delivering sensor readings to the upgraded cloud.
- Live Phase 1 readings are stored with `security_mode="rsa"`, as expected before gateway migration.

## Findings and remaining limitations

1. Restarting the cloud creates a short availability gap. One sensor transmission failed during the deliberate restart because the current sensor has no retry queue. Later readings resumed automatically.
2. The gateway still uses RSA and does not yet retrieve or pin the ML-KEM public key.
3. RSA private keys are still generated again when the cloud restarts. This is acceptable for the temporary legacy path because the gateway retrieves the current RSA public key for each reading.
4. Sensor-to-gateway communication remains plaintext and is outside the current ML-KEM migration scope.
5. Key rotation and previous-key overlap have not yet been implemented.
6. Monitoring remains based on application and container logs; metrics, dashboards, and alerting are still future maintenance work.
7. The test run reports a Starlette test-client deprecation warning, but it does not affect runtime behavior.

## Phase 1 conclusion

Phase 1 achieved its intended migration checkpoint. The cloud can process ML-KEM-768 traffic without breaking the existing RSA gateway, the ML-KEM identity survives restarts, downgrade behavior remains explicit, and the live sensor pipeline continues operating.

The next phase can update the gateway to retrieve and verify the ML-KEM public key, encapsulate a new shared secret for each reading, derive the AES key, and send version 2 envelopes. RSA should remain available only as an explicitly configured fallback until the ML-KEM gateway path has passed its functional, tamper, restart, and operational tests.
