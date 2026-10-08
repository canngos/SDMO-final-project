# Initial technical documentation

## Scope

This repository is the functional but intentionally incomplete legacy starting point for the course project. It contains a simulated device, an edge gateway, and a cloud service written in Python.

## Data flow

1. The sensor generates a temperature reading with a UUID and UTC timestamp.
2. The sensor sends the reading as plaintext HTTP/JSON to the gateway.
3. The gateway downloads the cloud's RSA-2048 public key.
4. The gateway creates a random AES-256 key and encrypts the reading with AES-GCM.
5. The gateway wraps the AES key using RSA-OAEP-SHA-256 and sends the envelope to the cloud.
6. The cloud unwraps the key, decrypts and validates the reading, and stores it in SQLite.

## Interfaces

Gateway:

- `POST /v1/readings`
- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /monitoring`
- `GET /v1/outbox/status`

Sensor:

- `GET /v1/readings/latest`: returns the latest locally generated raw reading.
- `GET /health`: reports that the sensor API process is running.
- `GET /ready`: reports whether the simulator has a reading and its forwarder is running.
- `GET /metrics`: reports process-local generation and gateway-send counters.
- `GET /monitoring`: reports local warning conditions such as repeated gateway send failures.

Cloud:

- `GET /v1/crypto/public-key`
- `GET /v2/crypto/public-key`
- `POST /v1/readings`
- `GET /v1/readings`
- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /monitoring`

## Baseline limitations

- RSA-2048 is classical and vulnerable to a sufficiently capable quantum computer.
- Cloud keys are generated at startup; rotation and durable key management are absent.
- The public-key response is not authenticated.
- Sensor-to-gateway traffic is plaintext and unauthenticated.
- Automated tests cover only two small cases.
- CI only installs dependencies and runs tests.
- Observability remains intentionally lightweight. The services expose JSON readiness, metrics, and monitoring endpoints, but there is no external dashboard or alerting system.
- The original containerized environment was only a local test setup. A separate automated Docker Compose smoke-test workflow now verifies image build, startup, health endpoints, and one end-to-end reading, but this is still not a production deployment.

## Operational observability

The project distinguishes liveness from readiness:

- `/health` means the service process can answer HTTP.
- `/ready` means the service can perform its own role at that moment.

The gateway does not become unready merely because the cloud is temporarily unavailable. Its durable SQLite outbox allows it to keep accepting readings until the outbox is unavailable, full, or the configured cryptographic mode is not usable. The sensor similarly remains ready while it can generate readings and run its retry loop. The cloud readiness check focuses on database access and key initialization.

Metrics and warning conditions are documented in [observability.md](observability.md). Metrics avoid plaintext readings and cryptographic secrets. In-memory counters reset on restart; SQLite-derived gauges such as gateway outbox depth and current cloud row counts survive while their databases survive.

These limitations are intentional starting conditions, not claims about a completed solution. The group should critically evaluate the implementation, discover additional issues, and record accepted, modified, and rejected AI output.

## Automated deployment test

Ahmed's deployment contribution adds a separate Docker Compose deployment workflow in `.github/workflows/deployment-test.yml`. It intentionally avoids modifying the main CI workflow because another branch is improving CI quality checks there.

The deployment workflow:

1. Checks out the repository in GitHub Actions.
2. Prepares a writable test `data` directory for the non-root containers.
3. Runs `docker compose config`.
4. Builds the cloud, gateway, and sensor images.
5. Starts the existing Docker Compose stack.
6. Runs `scripts/deployment_smoke_test.py`.
7. Checks the existing health endpoints.
8. Captures a sensor `message_id` and waits for that exact reading to appear in the cloud API.
9. Shows service status and failure logs.
10. Stops and cleans up the temporary environment.

This satisfies the course operations requirement at the educational test-environment level: automated build, automated startup, health checks, logs on failure, and end-to-end delivery verification. It does not create a production deployment platform.

Verification on 2026-10-06:

- 23 automated Python tests passed with one non-blocking Starlette deprecation warning.
- Docker Compose configuration validation passed.
- Docker image build passed.
- The Compose environment started successfully.
- Cloud, gateway, and sensor containers were healthy.
- The smoke test confirmed one sensor reading reached cloud storage.
- The local test environment was stopped afterward.
