# Operational Observability

## Purpose

This project keeps observability lightweight and local to the existing FastAPI, SQLite, and Docker Compose architecture. It adds readiness checks, JSON metrics, and warning conditions without Prometheus, Grafana, Kubernetes, external brokers, or notification services.

## Liveness and readiness

`GET /health` is liveness only. It answers whether the service process can respond to HTTP. It should stay simple and should not fail just because another service is temporarily unavailable.

`GET /ready` answers whether the service can currently perform its own role. It returns HTTP 200 when ready and HTTP 503 when not ready, with a small JSON explanation.

Readiness semantics:

- Sensor: configuration is loaded, a latest reading exists, and the background forwarder task is running. Gateway availability is not required because the sensor can retry the same reading.
- Gateway: configuration is loaded, the selected cryptographic mode is usable, and the SQLite outbox is accessible and has capacity. Cloud availability is not required because the gateway can safely queue readings while the cloud is temporarily unavailable.
- Cloud: configuration is loaded, the SQLite store is accessible, and RSA and ML-KEM key material has been initialized.

## Metrics

Each service exposes `GET /metrics` as a JSON endpoint. Metrics are intentionally process-local unless they are derived from SQLite state. In-memory counters reset when the service restarts.

Sensor metrics include:

- `readings_generated_total`
- `gateway_send_successes_total`
- `gateway_send_failures_total`
- `consecutive_gateway_failures`
- `start_time` and `uptime_seconds`

Gateway metrics include:

- `readings_received_total`
- `readings_accepted_total`
- `readings_rejected_total`
- `cloud_delivery_successes_total`
- `cloud_delivery_failures_total`
- `duplicate_deliveries_total`
- `crypto_key_failures_total`
- `outbox_pending`
- `outbox_oldest_pending_seconds`
- `outbox_retry_attempts_total`
- `crypto_mode`, `crypto_ready`, `start_time`, and `uptime_seconds`

Cloud metrics include:

- `envelopes_received_total`
- `rsa_envelopes_received_total`
- `mlkem_envelopes_received_total`
- `readings_persisted_total`
- `duplicate_deliveries_total`
- `validation_failures_total`
- `rsa_ingest_rejections_total`
- `readings_stored_current`
- `rsa_readings_stored_current`
- `mlkem_readings_stored_current`
- `allow_rsa_ingest`, `start_time`, and `uptime_seconds`

Metrics do not expose plaintext sensor readings, private keys, ML-KEM shared secrets, AES keys, or environment variable secrets.

## Monitoring warnings

Each service exposes `GET /monitoring`. The response has `status: "ok"` when no warning conditions are active and `status: "warning"` with a list of warning objects when attention is needed.

Warning conditions:

- Sensor: repeated gateway send failures, controlled by `SENSOR_FAILURE_WARNING_THRESHOLD`.
- Gateway: failed readiness, outbox depth at or above `GATEWAY_OUTBOX_WARNING_THRESHOLD`, or repeated cloud delivery failures controlled by `GATEWAY_DELIVERY_FAILURE_WARNING_THRESHOLD`.
- Cloud: failed readiness or repeated validation failures controlled by `CLOUD_VALIDATION_FAILURE_WARNING_THRESHOLD`.

These warnings are local inspection aids for the course demo. They do not send email, pages, or external notifications.

## Local inspection

```bash
curl http://localhost:8000/ready
curl http://localhost:8001/ready
curl http://localhost:8002/ready

curl http://localhost:8000/metrics
curl http://localhost:8001/metrics
curl http://localhost:8002/metrics

curl http://localhost:8000/monitoring
curl http://localhost:8001/monitoring
curl http://localhost:8002/monitoring
```

The deployment smoke test also checks health, readiness, metrics, monitoring, outbox status, and one end-to-end delivered sensor message:

```bash
python scripts/deployment_smoke_test.py
```

## Demonstrating a warning

To demonstrate gateway outbox and delivery warnings locally, start the stack and temporarily stop the cloud:

```bash
docker compose stop cloud
curl http://localhost:8001/metrics
curl http://localhost:8001/monitoring
docker compose start cloud
```

The gateway remains live and can keep accepting readings until the outbox reaches capacity. Readiness only fails when the gateway cannot safely perform its own role, such as when the outbox is full or cryptographic validation has failed.

## Limitations

- In-memory counters reset on service restart.
- The monitoring endpoint reports local warning conditions only; it is not a production alerting system.
- SQLite is suitable for the course demo but not a distributed production metrics store.
- The sensor-to-gateway path remains plaintext, as documented in the ML-KEM migration notes.
- Human review is still required before merge.
