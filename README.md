# Legacy Temperature Sensor Edge-Cloud Baseline

This is the intentionally incomplete starting system for a Software Development, Maintenance & Operations course project. A simulated temperature sensor sends JSON readings to an edge gateway. The gateway protects each reading with classical RSA-2048 key transport and AES-GCM, then forwards it to a cloud service that stores it in SQLite.

The baseline is functional, but it is not the final solution. RSA is vulnerable to a sufficiently capable quantum computer, test coverage is deliberately limited, CI only runs the small test suite, and operations support consists of basic console logs and a liveness endpoint. Those gaps are maintenance work to discover, evaluate, and improve with later AI prompts.

## Architecture

```mermaid
flowchart LR
    S[Simulated temperature sensor] -->|legacy HTTP + JSON| G[Edge gateway]
    G -->|fetch RSA public key| C[Cloud service]
    G -->|RSA-wrapped AES key + AES-GCM payload| C
    C --> D[(SQLite)]
```

The gateway generates a random AES-256 key for each reading, encrypts the reading with AES-GCM, and wraps the AES key with the cloud's RSA-2048 public key using OAEP-SHA-256. This is a classical hybrid-encryption baseline that can later be migrated to ML-KEM.

## Quick start with Docker

Requirements: Docker Desktop with Compose.

```bash
docker compose up --build
```

The simulator sends one reading every three seconds. Useful endpoints:

- Latest raw sensor reading: <http://localhost:8002/v1/readings/latest>
- Sensor health: <http://localhost:8002/health>
- Cloud readings: <http://localhost:8000/v1/readings>
- Cloud health: <http://localhost:8000/health>
- Gateway health: <http://localhost:8001/health>

Stop the environment with `docker compose down`. Add `-v` only when you intentionally want to delete the stored readings.

## Local development

Use Python 3.12 or newer.

```bash
python -m venv .venv
# PowerShell: .venv\Scripts\Activate.ps1
# Bash: source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
```

Run each process in a separate terminal:

```bash
uvicorn temperature_pqc.services.cloud:app --port 8000
uvicorn temperature_pqc.services.gateway:app --port 8001
uvicorn temperature_pqc.services.sensor:app --port 8002
```

Send one manual reading:

```bash
curl -X POST http://localhost:8001/v1/readings \
  -H "Content-Type: application/json" \
  -d '{"sensor_id":"sensor-01","temperature_c":21.5,"measured_at":"2026-09-22T10:00:00Z"}'
```

## Coursework evidence

- [Initial technical documentation](docs/technical-documentation.md)
- [Baseline notes](docs/baseline-and-migration.md)
- [AI-assisted development log](docs/ai-assisted-development-log.md)
- [Seven-week project plan](docs/project-plan.md)

The AI log deliberately marks human review as pending. Group members must run the system, find problems, review generated code, and record what they accept, modify, or reject. Do not use AI to write the individual reflection; the course brief explicitly prohibits that.

## Intentionally incomplete areas

- Classical RSA-2048 key transport is not post-quantum secure.
- Sensor-to-gateway traffic is plaintext.
- Test coverage is limited to a crypto round-trip and one validation rule.
- CI has no linting, security scanning, coverage threshold, image build, or deployment.
- Monitoring has no metrics, dashboards, alerts, or readiness checks.
- Cloud RSA keys are regenerated whenever the process restarts.
- The gateway does not authenticate the public-key response.
- SQLite supports the demo workload, not a distributed production deployment.

