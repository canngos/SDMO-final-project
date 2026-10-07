# ML-KEM Migration — Phase 2 Summary

## Phase objective

Phase 2 added ML-KEM-768 support to the edge gateway. The gateway can now create and send the version 2 ML-KEM envelope implemented by the cloud during Phase 1. The migration remains controlled: RSA is still the default mode, and ML-KEM must be enabled deliberately with a pinned cloud public-key fingerprint.

This phase focused on gateway implementation and an isolated canary test. It did not permanently switch the normal gateway or disable the RSA compatibility path.

## System state after Phase 2

- The sensor-to-gateway API remains unchanged.
- The cloud continues accepting version 1 RSA and version 2 ML-KEM envelopes.
- The gateway supports explicit `rsa` and `mlkem` operating modes.
- RSA remains the default mode until an operator enables the ML-KEM canary.
- ML-KEM mode requires an approved cloud public-key fingerprint.
- A fresh ML-KEM shared secret is encapsulated for every reading.
- The gateway never changes algorithms automatically after an error.
- A reading sent by an isolated Phase 2 gateway canary was stored successfully with `security_mode="mlkem"`.

## Gateway configuration

Two gateway settings were introduced:

```text
CRYPTO_MODE=rsa
EXPECTED_MLKEM_KEY_ID=
```

`CRYPTO_MODE` accepts only:

- `rsa`: use the original version 1 RSA-OAEP and AES-256-GCM flow.
- `mlkem`: use the version 2 ML-KEM-768, HKDF-SHA-256, and AES-256-GCM flow.

When `CRYPTO_MODE=mlkem`, `EXPECTED_MLKEM_KEY_ID` is mandatory. It must be the 64-character lowercase SHA-256 fingerprint of the approved cloud ML-KEM public key. The gateway refuses to start in ML-KEM mode when the value is missing or malformed.

The cloud publishes its public-key document through:

```text
GET /v2/crypto/public-key
```

For the local simulation, the fingerprint can be retrieved with PowerShell:

```powershell
(Invoke-RestMethod http://localhost:8000/v2/crypto/public-key).key_id
```

The retrieved value must be reviewed and placed in the project `.env` file before recreating the gateway. In a production deployment, the initial fingerprint would need to be distributed through a trusted administrative channel rather than accepted automatically from an unauthenticated network response.

## ML-KEM gateway flow

For each reading in ML-KEM mode, the gateway performs the following sequence:

1. Confirms at startup that the installed cryptographic backend supports ML-KEM-768.
2. Retrieves the version 2 ML-KEM public-key document from the cloud.
3. Validates the document against the strict ML-KEM public-key model.
4. Compares the received `key_id` with `EXPECTED_MLKEM_KEY_ID`.
5. Decodes the raw public key and independently recalculates its SHA-256 fingerprint.
6. Rejects the key if the declared, calculated, and configured fingerprints do not match.
7. Loads the validated ML-KEM-768 public key through the `cryptography` library.
8. Encapsulates a fresh 32-byte shared secret and creates the ML-KEM ciphertext.
9. Derives an AES-256 key using HKDF-SHA-256 and the agreed protocol context.
10. Generates a fresh 12-byte AES-GCM nonce.
11. Encrypts the serialized sensor reading with AES-256-GCM.
12. Authenticates the protocol version, algorithm, key identifier, and message identifier as associated data.
13. Sends the version 2 envelope to the cloud reading endpoint.

The envelope contains the public metadata and encrypted values required by the cloud:

```json
{
  "version": 2,
  "alg": "ML-KEM-768+HKDF-SHA256+AES-256-GCM",
  "key_id": "approved SHA-256 fingerprint",
  "message_id": "reading UUID",
  "kem_ciphertext": "base64",
  "nonce": "base64",
  "ciphertext": "base64"
}
```

The gateway does not log the shared secret, derived AES key, plaintext reading, or private key material.

## Public-key pinning

Pinning prevents the gateway from accepting an arbitrary ML-KEM public key simply because it was returned by the configured HTTP endpoint.

The gateway performs two checks:

1. The `key_id` in the cloud response must match the operator-configured `EXPECTED_MLKEM_KEY_ID`.
2. The gateway recalculates SHA-256 over the received raw public-key bytes and verifies that the result matches the same `key_id`.

The second check prevents an attacker or configuration error from combining an approved fingerprint string with different public-key bytes. Constant-time comparison is used for the fingerprint comparisons.

## Failure and fallback behavior

Phase 2 intentionally avoids automatic fallback.

| Scenario | Gateway behavior |
|---|---|
| `CRYPTO_MODE` is invalid | Configuration fails instead of selecting a default algorithm. |
| ML-KEM mode has no key pin | Gateway startup fails with a configuration error. |
| Pinned key ID is malformed | Gateway startup fails with a configuration error. |
| Cloud public-key response is malformed | Forwarding is rejected and no RSA request is made. |
| Received key ID differs from the pin | Forwarding fails closed and the gateway cryptographic health becomes unavailable. |
| Public-key bytes do not match their declared fingerprint | Forwarding fails closed and the substituted key is not used. |
| Cloud is unreachable or returns an error | The request fails using the configured mode; RSA is not attempted. |
| ML-KEM encryption or cloud ingestion fails | The failure is reported; RSA is not attempted. |
| Operator explicitly selects RSA | The unchanged version 1 RSA flow is used. |

After a key-validation failure, the gateway health endpoint returns HTTP 503. This makes the security failure visible to Docker health checks and operators. Ordinary cloud network failures return a forwarding error but do not silently change the selected cryptographic mode.

## Logging and observability

The gateway logs the configured mode and uses the existing `message_id` correlation value. Phase 2 adds or extends events including:

- `mlkem_capability_verified`
- `gateway_started`
- `gateway_reading_received`
- `cloud_key_request`
- `mlkem_envelope_created`
- `cloud_key_received`
- `cloud_forward_attempt`
- `cloud_forward_succeeded`
- `mlkem_key_validation_failed`
- `gateway_forward_failed`

The gateway health response also reports the selected `crypto_mode` when the cryptographic configuration is ready.

## Verification performed

The complete suite contained 14 passing tests after Phase 2. Gateway-specific coverage verifies:

- A gateway with a valid pinned key sends an ML-KEM envelope that the cloud key can decrypt.
- Explicit RSA mode preserves the original gateway behavior.
- A mismatched key pin fails closed without requesting the RSA endpoint.
- Public-key bytes with a falsified approved `key_id` are detected by fingerprint recalculation.
- A cloud network or server failure does not trigger RSA fallback.
- ML-KEM mode rejects a missing key pin.
- Existing cloud, RSA cryptography, model validation, persistence, and tamper-rejection tests remain passing.

The test run reports one Starlette test-client deprecation warning. It does not affect the running services or cryptographic behavior.

## Docker canary evidence

The Phase 2 gateway image was built successfully. An isolated one-off gateway container was then configured with:

- `CRYPTO_MODE=mlkem`
- The fingerprint published by the running cloud as `EXPECTED_MLKEM_KEY_ID`

The canary completed the following live path:

1. Verified ML-KEM-768 backend availability.
2. Retrieved the cloud ML-KEM public key.
3. Verified the configured and calculated key fingerprints.
4. Created an ML-KEM version 2 envelope.
5. Sent the envelope through the Docker network.
6. Received HTTP 200 from the cloud and returned HTTP 202 from the gateway.
7. Stored the canary message `5ae413ae-aacf-48c6-a275-396651430db6` with `security_mode="mlkem"`.

The normal gateway configuration was not changed during the canary. The cloud was started temporarily for the check and then stopped again, restoring all project containers to their previous stopped state.

## Enabling the reviewed ML-KEM canary

After human review, the normal gateway can be deliberately switched by adding the approved values to `.env`:

```text
CRYPTO_MODE=mlkem
EXPECTED_MLKEM_KEY_ID=<64-character key fingerprint>
```

Then recreate the gateway:

```powershell
docker compose up -d --build --force-recreate gateway
```

The gateway and cloud logs should be followed during the canary:

```powershell
docker compose logs --follow gateway cloud
```

A successful sensor message should appear in cloud storage with `security_mode="mlkem"`.

## Remaining limitations and next steps

1. The normal gateway still defaults to RSA until the reviewed canary is explicitly enabled.
2. RSA ingestion remains enabled on the cloud for migration and rollback.
3. The initial ML-KEM fingerprint is retrieved manually; production deployment would require an authenticated distribution process.
4. ML-KEM key rotation and a controlled previous-key overlap window are not implemented.
5. The sensor currently has no durable retry queue, so a reading can be lost during a service restart.
6. Sensor-to-gateway traffic remains plaintext and outside the current ML-KEM migration scope.
7. Operational monitoring still relies on logs and health checks rather than metrics, dashboards, and alerts.

The next migration checkpoint is to enable the normal gateway as an ML-KEM canary, observe live sensor traffic, demonstrate an explicit operator-controlled rollback to RSA, restore ML-KEM, and finally disable RSA ingestion only after the ML-KEM path is accepted as stable.

## Phase 2 conclusion

Phase 2 achieved the gateway-side ML-KEM integration while preserving a controlled and reviewable migration. The gateway can create valid ML-KEM-768 envelopes, authenticates the cloud public key through pinning and independent fingerprint calculation, fails closed on key-validation problems, and never silently downgrades after an error. Automated tests and an isolated Docker canary confirmed interoperability with the Phase 1 cloud implementation.
