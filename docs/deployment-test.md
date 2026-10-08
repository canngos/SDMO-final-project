# Automated Docker Deployment Test

## Purpose

This deployment test is Ahmed Aly's deployment-focused contribution. It adds an automated check that builds the Docker images, starts the existing Docker Compose application as a temporary test environment, verifies the running services, proves that one sensor reading reaches the cloud, and then cleans up.

This is an educational test deployment. It is not a production deployment and does not add a real cloud hosting platform, secrets manager, dashboard, or alerting system.

## What was added

- `.github/workflows/deployment-test.yml`: a separate GitHub Actions workflow for Docker Compose deployment verification.
- `scripts/deployment_smoke_test.py`: a small Python script that checks the live services using only the Python standard library.

The main `.github/workflows/ci.yml`, `pyproject.toml`, and `src/temperature_pqc/crypto.py` were intentionally not changed because Laura's branch is working on CI quality checks in those files.

## How the workflow works

1. GitHub Actions checks out the repository.
2. It creates a writable `data` directory for the Linux runner because the containers run as a non-root user.
3. It validates the Docker Compose configuration with `docker compose config`.
4. It builds the Docker images with `docker compose build`.
5. It starts the cloud, gateway, and sensor services with `docker compose up -d`.
6. It runs `python scripts/deployment_smoke_test.py`.
7. The smoke test waits for:
   - cloud `GET /health`
   - gateway `GET /health`
   - sensor `GET /health`
   - cloud, gateway, and sensor `GET /ready`
   - sensor `GET /v1/readings/latest`
   - cloud `GET /v1/readings`
   - gateway `GET /v1/outbox/status`
   - cloud, gateway, and sensor `GET /metrics`
   - cloud, gateway, and sensor `GET /monitoring`
8. The smoke test captures a sensor `message_id` and waits until the same message appears in cloud storage.
9. It confirms the lightweight monitoring endpoints report no active warnings in the normal deployment path.
10. If a step fails, the workflow prints service logs to help debugging.
11. The workflow always stops the containers with `docker compose down --volumes --remove-orphans` and removes the temporary `data` directory.

## How to run locally

From the repository root:

```bash
docker compose config
docker compose build
docker compose up -d
python scripts/deployment_smoke_test.py
docker compose down --volumes --remove-orphans
```

On GitHub, it can run automatically for pull requests, manually with `workflow_dispatch`, and on pushes to `feature/ahmed-deployment`.

## Verification completed on 2026-10-06

- `python -m pytest`: passed, 23 tests.
- `docker compose config`: passed.
- `docker compose build`: passed for cloud, gateway, and sensor images.
- `docker compose up -d`: cloud, gateway, and sensor started and became healthy.
- `python scripts/deployment_smoke_test.py`: passed. It verified message `920cf57d-d5de-40db-87e9-ebea32ae533b` reached the cloud with `security_mode=rsa`.
- `docker compose ps`: showed all three services as healthy.
- `docker compose down --volumes --remove-orphans`: stopped and removed the test containers and network.

One non-blocking Starlette deprecation warning appeared during the Python tests. No secret values were printed by the new smoke test. The application logs printed public message IDs, key IDs, service names, algorithms, and status codes, but not private keys, shared secrets, AES keys, or plaintext reading payloads.

## Limitations and human review

- The workflow verifies the default Docker Compose mode, which is RSA unless environment variables are changed. ML-KEM behavior is already covered by the existing automated tests and previous migration documentation, but this deployment smoke test does not dynamically pin and start an ML-KEM canary.
- The workflow uses Docker Compose as a temporary test environment. It does not deploy to a long-running server.
- The workflow must still be reviewed after it runs on GitHub Actions, because local Docker success does not guarantee the hosted runner will behave exactly the same.
- Laura should review the final pull request interaction because her branch changes the main CI workflow and development dependencies.
