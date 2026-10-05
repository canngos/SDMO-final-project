# ML-KEM Migration — Phase 3 Summary

## Phase objective

Phase 3 completed the operational migration to ML-KEM and added a controlled fallback strategy and reliable store-and-forward delivery. The normal gateway is now configured for ML-KEM, legacy RSA ingestion can be disabled at the cloud, and temporary delivery failures no longer cause sensor readings to be discarded.

The fallback design remains explicit and operator controlled. The gateway never changes from ML-KEM to RSA automatically in response to a network, cryptographic, or server failure.

## Final system state

- The sensor continues producing a reading approximately every three seconds.
- The sensor retries the same reading and `message_id` when the gateway is temporarily unavailable.
- The gateway persists accepted readings in a SQLite outbox before acknowledging them.
- A background worker sends pending readings using only the configured `CRYPTO_MODE`.
- Failed deliveries remain pending and are retried with bounded exponential backoff.
- Pending readings survive gateway container recreation in the local bind-mounted `data` folder.
- HTTP 409 duplicate responses are treated as successful delivery because the cloud already stores the same `message_id`.
- The cloud accepts RSA only when `ALLOW_RSA_INGEST=true`.
- The final deployment configuration uses `CRYPTO_MODE=mlkem` and `ALLOW_RSA_INGEST=false`.
- Cloud API results distinguish sensor measurement time, cloud arrival time, and total delivery delay.

## Migration and fallback strategy

The project satisfies the requirement to preserve legacy compatibility where appropriate while providing a clearly justified migration and fallback strategy.

During migration, the cloud can accept both version 1 RSA and version 2 ML-KEM envelopes. This allows a reviewed gateway to be switched deliberately between the two protocols. After ML-KEM is accepted as stable, `ALLOW_RSA_INGEST=false` enforces the post-quantum path by rejecting legacy RSA envelopes.

Fallback to RSA requires two explicit operator actions:

1. Set `ALLOW_RSA_INGEST=true` for the cloud and recreate the cloud container.
2. Set `CRYPTO_MODE=rsa` for the gateway and recreate the gateway container.

Restoring ML-KEM similarly requires setting `CRYPTO_MODE=mlkem`, retaining the approved `EXPECTED_MLKEM_KEY_ID`, recreating the gateway, and then disabling RSA ingestion again after successful verification.

This restart-based control is intentional. Cryptographic policy is fixed at service startup, which makes a mode change visible, auditable, and resistant to silent downgrade attacks. Network errors, HTTP errors, invalid public keys, or ML-KEM failures therefore result in queued retries rather than an automatic switch to RSA.

## Durable gateway outbox

The gateway uses a local SQLite outbox to provide a durable handoff:

1. The sensor sends a validated reading to the gateway.
2. The gateway stores the raw reading and its original `message_id` in the outbox.
3. Only after the local write succeeds does the gateway return HTTP 202.
4. The background worker encrypts the pending reading using the configured protocol and sends it to the cloud.
5. On success, or an idempotent HTTP 409 duplicate response, the gateway deletes the outbox entry.
6. On failure, the gateway retains the entry and schedules another attempt using bounded exponential backoff.

The outbox is intentionally written before cloud delivery. This removes the crash window in which a gateway could acknowledge a reading and then stop before saving a failed delivery. The outbox contains plaintext sensor readings temporarily and must therefore be treated as sensitive edge storage. Its database is protected with owner-only file permissions where supported and persisted in the project's local bind-mounted `data` folder so it can be inspected directly during the simulation.

Outbox state is observable through:

```text
GET /v1/outbox/status
```

The response reports the number of pending readings, the age of the oldest pending reading, and the cumulative number of scheduled retries. The gateway health response also reports the current cryptographic mode and pending count.

## Sensor retry behavior

If the sensor cannot obtain an HTTP 202 response from the gateway, it retains the current reading instead of generating a replacement. It retries the same payload and `message_id` after `SENSOR_RETRY_SECONDS`. This complements the gateway outbox:

- Sensor-to-gateway failures are handled by the sensor retry loop.
- Gateway-to-cloud failures are handled by the durable gateway outbox.
- Cloud duplicate protection makes uncertain repeated delivery idempotent.

## Timestamp clarification

Recovered readings preserve their original sensor event time:

- `measured_at`: when the sensor created the reading.
- `cloud_received_at`: when the cloud successfully stored it.
- `delivery_delay_seconds`: the difference between those timestamps.

Several recovered readings can legitimately have nearly identical cloud arrival times because the gateway drains a backlog quickly. New cloud timestamps use UTC with fractional-second precision. Existing databases are migrated automatically from the former `received_at` column without deleting or rewriting stored timestamps.

## Tested fallback and recovery scenarios

The following operator tests were completed during Phase 3:

1. Live sensor data continued to be handled while the gateway was deliberately switched between RSA and ML-KEM modes.
2. RSA delivery succeeded while legacy ingestion was enabled.
3. With `ALLOW_RSA_INGEST=false`, an RSA envelope was rejected by the cloud with HTTP 403 as expected.
4. Returning the gateway to ML-KEM restored successful cloud delivery.
5. Readings accumulated during the rejection period were retained rather than discarded.
6. The retry queue drained successfully after a valid ML-KEM path was restored.
7. Recovered readings retained their original `measured_at` values and therefore preserved the sensor sampling intervals.

These results demonstrate operator-controlled fallback and recovery without introducing an external queue such as RabbitMQ.

## Automated verification coverage

The automated tests cover:

- Valid ML-KEM envelope creation and cloud decryption.
- Explicit RSA compatibility while RSA ingestion is allowed.
- RSA rejection when `ALLOW_RSA_INGEST=false`.
- ML-KEM public-key fingerprint pinning and recalculation.
- Missing, malformed, mismatched, or substituted key rejection.
- Tampered ML-KEM ciphertext rejection.
- Persistent ML-KEM key material across cloud restarts.
- Network and HTTP failure handling without automatic RSA fallback.
- Durable outbox recovery after reinitialization.
- Retry counting and bounded queue capacity.
- Duplicate-safe HTTP 409 handling.
- Pending RSA data being delivered with ML-KEM after an explicit gateway restart.
- Sensor reuse of the same `message_id` during gateway failure.
- Backward-compatible timestamp schema migration and delivery-delay calculation.

## Operational configuration

The important migration and retry settings are:

```text
CRYPTO_MODE=mlkem
EXPECTED_MLKEM_KEY_ID=<approved 64-character SHA-256 fingerprint>
ALLOW_RSA_INGEST=false
GATEWAY_OUTBOX_MAX_PENDING=1000
GATEWAY_RETRY_INITIAL_SECONDS=1
GATEWAY_RETRY_MAX_SECONDS=30
GATEWAY_OUTBOX_POLL_SECONDS=0.5
SENSOR_RETRY_SECONDS=1
```

Changing `CRYPTO_MODE` or `ALLOW_RSA_INGEST` requires recreating the corresponding container. This is acceptable for the simulation and makes security-policy changes deliberate.

## Final verification before commit

The final Phase 3 check produced the following evidence:

- Docker Compose configuration validation completed successfully.
- All 22 automated tests passed.
- One non-blocking Starlette test-client deprecation warning remains.
- Cloud, gateway, and sensor images built successfully.
- All three recreated services reached healthy status.
- The live gateway reported `crypto_mode="mlkem"`.
- The gateway outbox returned to zero pending readings.
- Docker inspection confirmed that `/app/data` in the gateway is bind-mounted from the project's local `data` folder.
- Sensor message `11323c16-440c-4b45-a292-3639ffee174e` appeared exactly once in cloud storage after the bind mount was verified.
- The stored record used `security_mode="mlkem"` and included both `cloud_received_at` and `delivery_delay_seconds`.

The temporary verification environment was stopped afterward without deleting persistent data or volumes.

## Remaining limitations

1. Sensor-to-gateway traffic remains plaintext because this project scopes ML-KEM to the gateway-to-cloud path.
2. The outbox has a configurable finite capacity; when full, the gateway returns HTTP 503 and the sensor continues retrying its current reading.
3. ML-KEM key rotation with a previous-key overlap window is not implemented.
4. Initial key-fingerprint distribution is manual and would require an authenticated administrative channel in production.
5. Monitoring uses structured logs, health endpoints, and outbox status rather than a full metrics and alerting platform.
6. The simulation uses one gateway worker. A production multi-worker deployment would require additional queue-claiming coordination.
7. RSA code remains available for a documented emergency rollback, although final cloud policy disables RSA ingestion.

## Phase 3 conclusion

Phase 3 completes the staged migration. ML-KEM is the enforced normal path, RSA remains an explicit and controlled compatibility option, and failures never trigger an automatic cryptographic downgrade. The sensor retry loop, durable gateway outbox, exponential retry scheduling, persistent volumes, and duplicate protection prevent temporary gateway, cloud, or policy failures from silently losing readings. Timestamp changes also make delayed delivery visible without altering the original measurement time.
