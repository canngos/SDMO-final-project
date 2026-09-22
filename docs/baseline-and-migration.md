# Baseline notes

## Initial architecture

The simulated sensor posts plaintext JSON to the edge gateway. The gateway retrieves the cloud's RSA-2048 public key, creates a random AES-256 key, encrypts the reading with AES-GCM, wraps the AES key with RSA-OAEP-SHA-256, and sends the envelope to the cloud. The cloud decrypts and validates the reading and stores it in SQLite.

This is a functional classical-cryptography baseline. RSA is the deliberately outdated key-establishment/key-transport mechanism that the project will later replace or augment with ML-KEM.

## Baseline work for the group

Run the system before modifying it and record reproducible evidence:

- Whether all three components start and readings reach the database.
- Current automated test count and coverage.
- Current CI stages and missing stages.
- Existing health, logging, and monitoring behavior.
- End-to-end latency and failure behavior when the cloud is unavailable.
- Security and maintenance findings in the generated code.

Do not write expected numbers into the report. Run measurements and preserve the commands and outputs. Add the exact prompts used for later changes to `ai-assisted-development-log.md`.

## Modernization status

ML-KEM integration, migration/fallback design, stronger automated tests, completed CI/CD, deployment hardening, and useful operational monitoring are intentionally **not implemented yet**. They are the next project stages.

