# ML-KEM Implementation, Migration, and Fallback Plan

## 1. Goal and scope

Replace RSA-2048 key transport on the gateway-to-cloud path with ML-KEM-768 while keeping the simulated sensor running throughout the migration. The cloud will temporarily accept both protocols so legacy and upgraded gateways can coexist. RSA fallback will be an explicit operator decision, never an automatic reaction to network or cryptographic errors.

The sensor-to-gateway protocol is outside this migration and remains unchanged. The sensor's `GET /v1/readings/latest` endpoint will be used to compare raw device readings with cloud-stored readings during every rollout phase.

## 2. Proposed decisions for approval

| Decision | Proposal | Reason |
|---|---|---|
| ML-KEM parameter set | ML-KEM-768 | Balanced security and performance; suitable for the course-sized system. |
| Python implementation | `cryptography>=48.0.1,<49` | Existing project dependency; current supported wheels expose the ML-KEM API. |
| Data encryption | Keep AES-256-GCM | ML-KEM establishes a shared secret; AES-GCM continues to protect the reading. |
| Key derivation | HKDF-SHA-256 | Derives a protocol-specific AES key and binds key use to contextual information. |
| Migration method | Cloud dual acceptance; gateway configured per algorithm | Supports old and new gateways without ambiguous automatic negotiation. |
| Gateway modes | `mlkem` and `rsa` | Small, explicit, and easy to audit. |
| Automatic fallback | Not allowed | Prevents network faults or attackers from silently forcing an RSA downgrade. |
| RSA availability | Controlled by `ALLOW_RSA_INGEST` on cloud | Allows a time-bounded migration window and a clear final disable switch. |
| Key trust | Gateway pins an expected ML-KEM `key_id` | Prevents accepting an arbitrary substituted public key from the HTTP endpoint. |
| Key persistence | Persist the ML-KEM private seed in the cloud data volume | Prevents every cloud restart from invalidating the published key. |

Implementation should begin only after these decisions are accepted or revised.

## 3. Target protocol

### 3.1 Cloud responsibilities

1. Load an existing ML-KEM-768 private seed or generate and securely persist one on first startup.
2. Publish the raw ML-KEM public key with its algorithm, protocol version, and `key_id`.
3. Keep the existing RSA public-key endpoint available during migration.
4. Accept either a version 1 RSA envelope or a version 2 ML-KEM envelope while `ALLOW_RSA_INGEST=true`.
5. Select the correct private key using `alg` and `key_id`.
6. Decapsulate the ML-KEM ciphertext, derive the AES key, authenticate and decrypt the reading, validate it, and store it.
7. Record `security_mode="mlkem"` or `security_mode="rsa"` with each stored reading.

### 3.2 Gateway responsibilities

1. Read `CRYPTO_MODE=mlkem|rsa` at startup.
2. In `mlkem` mode, verify that the local cryptography backend supports ML-KEM-768.
3. Retrieve the cloud ML-KEM public key and reject it unless its calculated `key_id` matches `EXPECTED_MLKEM_KEY_ID`.
4. Encapsulate a fresh shared secret for each sensor reading.
5. Derive an AES-256 key using HKDF-SHA-256.
6. Encrypt the reading using AES-GCM and send a version 2 envelope.
7. In `rsa` mode, use the unchanged version 1 RSA flow.
8. Never change from ML-KEM to RSA in response to a timeout, invalid key, decryption failure, or server error.

### 3.3 Version 2 ML-KEM envelope

The new envelope will contain:

```json
{
  "version": 2,
  "alg": "ML-KEM-768+HKDF-SHA256+AES-256-GCM",
  "key_id": "public-key fingerprint",
  "message_id": "UUID",
  "kem_ciphertext": "base64",
  "nonce": "base64",
  "ciphertext": "base64"
}
```

The canonical associated data for AES-GCM will include `version`, `alg`, `key_id`, and `message_id`. Any change to those fields must cause authentication failure.

The existing RSA envelope and its cryptographic behavior will not be changed during the first migration step. This preserves compatibility with existing gateways.

## 4. Code-change plan

### Step 1 - Dependency and startup capability check

- Change the cryptography dependency to `cryptography>=48.0.1,<49`.
- Add a startup check that verifies ML-KEM-768 backend support.
- Fail clearly at startup when `CRYPTO_MODE=mlkem` is selected but ML-KEM is unavailable.
- Keep RSA mode runnable during development.

Acceptance criteria:

- Supported local and container environments pass the capability check.
- Unsupported environments produce a clear configuration error rather than silently using RSA.

### Step 2 - Separate protocol models

- Rename the current key and envelope models to explicit RSA models.
- Add ML-KEM public-key and envelope models.
- Use a discriminated union based on `alg` for cloud ingestion.
- Preserve the existing version 1 RSA JSON format.
- Reject unknown versions, algorithms, missing fields, invalid Base64, and oversized input.

Acceptance criteria:

- Existing RSA payloads still validate.
- ML-KEM payloads validate only with the exact version 2 fields.
- Mixed RSA/ML-KEM field combinations are rejected.

### Step 3 - ML-KEM cryptographic component

- Add an `MlKemCloudKeyPair` abstraction alongside `LegacyCloudKeyPair`.
- Use `MLKEM768PrivateKey` and `MLKEM768PublicKey` from `cryptography`.
- Implement public-key serialization, encapsulation, decapsulation, `key_id` calculation, HKDF, and AES-GCM encryption/decryption.
- Keep cryptographic errors generic at the API boundary.
- Never log the private seed, shared secret, or derived AES key.

Acceptance criteria:

- Gateway and cloud derive the same AES key.
- A valid reading decrypts correctly.
- Modified KEM ciphertext, AES ciphertext, nonce, or associated data is rejected.

### Step 4 - Cloud dual-protocol support

- Load persistent ML-KEM key material during cloud startup.
- Add an ML-KEM public-key endpoint.
- Update the reading endpoint to dispatch by the validated envelope algorithm.
- Add `ALLOW_RSA_INGEST`, initially `true`.
- Reject RSA envelopes with a clear policy response when the flag becomes `false`.
- Preserve duplicate-message protection in SQLite.

Acceptance criteria:

- Old RSA gateway to new cloud succeeds while RSA ingestion is enabled.
- New ML-KEM gateway to new cloud succeeds.
- RSA is rejected when RSA ingestion is disabled.

### Step 5 - Gateway algorithm selection

- Add `CRYPTO_MODE`, defaulting to `rsa` for the first deployment.
- Add `EXPECTED_MLKEM_KEY_ID` for public-key pinning.
- Select exactly one sender implementation at startup.
- Include algorithm and key ID in logs, but never key material.
- Refresh a rotated public key only under an explicit key-rotation procedure.

Acceptance criteria:

- `CRYPTO_MODE=rsa` preserves current behavior.
- `CRYPTO_MODE=mlkem` sends only ML-KEM envelopes.
- A key-ID mismatch stops forwarding and does not invoke RSA.

### Step 6 - Configuration and deployment

- Add all new settings to `.env.example`.
- Mount the cloud key directory through the existing persistent volume.
- Keep Compose health-gated startup ordering.
- Add algorithm mode and migration instructions to the README.
- Do not change the running sensor API or its forwarding contract.

Acceptance criteria:

- `docker compose up --build` starts all services in the configured mode.
- All three services become healthy.
- The sensor's raw `message_id` appears in cloud storage with the expected `security_mode`.

## 5. Migration stages

| Stage | Cloud | Gateway | Purpose and exit condition |
|---|---|---|---|
| 0. Baseline | RSA only | RSA | Save baseline tests, latency, payload size, and successful sensor message IDs. |
| 1. Dual-capable cloud | RSA + ML-KEM, RSA allowed | RSA | Confirm old behavior is unchanged after the cloud upgrade. |
| 2. ML-KEM canary | RSA + ML-KEM, RSA allowed | ML-KEM on one test gateway | Run functional, tamper, restart, and performance tests. |
| 3. Migration | RSA + ML-KEM, RSA allowed | ML-KEM by default; named legacy gateways remain RSA | Confirm ML-KEM success and identify every remaining RSA sender. |
| 4. PQC enforcement | RSA + ML-KEM, RSA disabled | ML-KEM | Confirm RSA attempts are rejected and no required gateway depends on RSA. |
| 5. Cleanup | ML-KEM; RSA code retained temporarily but disabled | ML-KEM | Document rollback expiry and later remove RSA after project approval. |

Each stage requires recorded evidence before moving to the next stage. Do not combine the cloud and gateway changes into one unobservable cutover.

## 6. Fallback and failure scenarios

| Scenario | Required behavior |
|---|---|
| ML-KEM fails during development or canary | Keep the gateway configuration on `rsa`; fix and retest ML-KEM before resuming migration. |
| Serious ML-KEM defect after rollout | Operator sets cloud `ALLOW_RSA_INGEST=true`, sets affected gateway `CRYPTO_MODE=rsa`, and restarts it. Record the reason, approver, start time, and planned end time. |
| Cloud is temporarily unreachable | Retry or report failure using the same configured algorithm. Do not fall back to RSA. |
| Cloud returns 4xx/5xx | Report the failure. Do not fall back to RSA. |
| ML-KEM public key is malformed | Reject it and mark the gateway unhealthy. Do not fall back. |
| Pinned `key_id` does not match | Treat it as a possible key-substitution or deployment error. Stop forwarding and alert. |
| KEM ciphertext or AES payload is modified | Reject with a generic invalid-envelope response. Do not retry with RSA. |
| Cloud key rotates during an in-flight message | Cloud retains the previous key for a short overlap window and selects it by `key_id`. |
| Gateway retries after an uncertain response | Reuse the original reading and `message_id`; cloud duplicate protection prevents a second stored record. |
| Old gateway communicates with upgraded cloud | RSA succeeds only while `ALLOW_RSA_INGEST=true`. |
| Upgraded gateway must communicate with old cloud | Operator explicitly configures `CRYPTO_MODE=rsa`; no automatic negotiation or downgrade. |
| Local ML-KEM backend is unsupported | ML-KEM mode fails at startup. The deployment must be deliberately changed to RSA mode or upgraded. |

### Rollback procedure

1. Confirm that the problem is caused by the ML-KEM path rather than a general network or cloud outage.
2. Record the failed version, evidence, affected gateways, and reason for rollback.
3. Enable RSA ingestion on the cloud.
4. Change only affected gateways to `CRYPTO_MODE=rsa`.
5. Restart and verify a raw sensor `message_id` reaches the cloud with `security_mode="rsa"`.
6. Keep ML-KEM failure evidence and open a corrective task.
7. Restore ML-KEM after the fix passes the full test matrix.
8. Disable RSA ingestion again.

## 7. Required test matrix

### Cryptographic unit tests

- ML-KEM encapsulation/decapsulation produces matching shared secrets.
- Full ML-KEM + HKDF + AES-GCM round trip.
- Wrong private key or unknown `key_id` is rejected.
- Modified KEM ciphertext is rejected through payload authentication failure.
- Modified AES ciphertext, nonce, algorithm, version, key ID, or message ID is rejected.
- Invalid Base64 and incorrect key/ciphertext lengths are rejected.
- RSA version 1 tests remain passing.

### Migration and integration tests

- Old RSA gateway -> dual-capable cloud.
- ML-KEM gateway -> dual-capable cloud.
- RSA gateway -> cloud with RSA disabled.
- ML-KEM gateway with incorrect pinned key ID.
- Cloud restart with persistent ML-KEM key.
- Cloud key rotation with current and previous key IDs.
- Duplicate/retried reading is stored once.
- Network outage does not trigger RSA downgrade.
- Raw sensor message ID is traceable through gateway and cloud.

### Operational and performance tests

- Compare RSA and ML-KEM median and p95 forwarding latency.
- Compare request sizes and gateway CPU/memory.
- Confirm health checks in both modes.
- Confirm logs show algorithm, message ID, and failure category without secrets.
- Confirm container restart behavior and key persistence.

## 8. Evidence to retain for the report

- Exact AI prompts and summarized outputs.
- Before/after architecture and protocol diagrams.
- Test commands and complete results.
- RSA and ML-KEM performance measurements.
- Examples of successful ML-KEM delivery and rejected tampered messages.
- A demonstrated rollback to RSA and restoration to ML-KEM.
- Findings that were accepted, modified, or rejected during human review.
- Remaining risks, especially unauthenticated sensor-to-gateway traffic and the limitations of an educational application protocol.

## 9. Implementation order after approval

1. Dependency check and ML-KEM proof-of-concept tests.
2. Versioned models and ML-KEM crypto component.
3. Cloud dual-protocol support and persistent key loading.
4. Gateway mode selection and public-key pinning.
5. Migration/fallback tests.
6. Compose configuration and documentation.
7. Baseline-versus-ML-KEM measurements.
8. Canary migration, rollback demonstration, and final PQC enforcement.

## 10. References

- [NIST FIPS 203 - Module-Lattice-Based Key-Encapsulation Mechanism Standard](https://csrc.nist.gov/pubs/fips/203/final)
- [NIST SP 800-227 - Recommendations for Key-Encapsulation Mechanisms](https://csrc.nist.gov/pubs/sp/800/227/final)
- [NIST crypto-agility guidance](https://csrc.nist.gov/pubs/cswp/39/upd1/considerations-for-achieving-crypto-agility/final)
- [Python cryptography ML-KEM documentation](https://cryptography.io/en/48.0.1/hazmat/primitives/asymmetric/mlkem/)
