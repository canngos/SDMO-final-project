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

Cloud:

- `GET /v1/crypto/public-key`
- `POST /v1/readings`
- `GET /v1/readings`
- `GET /health`

## Baseline limitations

- RSA-2048 is classical and vulnerable to a sufficiently capable quantum computer.
- Cloud keys are generated at startup; rotation and durable key management are absent.
- The public-key response is not authenticated.
- Sensor-to-gateway traffic is plaintext and unauthenticated.
- Automated tests cover only two small cases.
- CI only installs dependencies and runs tests.
- Logs are plain console messages and there are no metrics, dashboards, alerts, or readiness checks.
- The containerized environment is a local test setup, not an automated deployment pipeline.

These limitations are intentional starting conditions, not claims about a completed solution. The group should critically evaluate the implementation, discover additional issues, and record accepted, modified, and rejected AI output.

