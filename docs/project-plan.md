# Seven-week project plan

Keep the scope small and preserve evidence after each stage. Suggested ownership rotates so that no person is the only reviewer of their own component.

| Week | Outcome | Evidence |
|---|---|---|
| 1 | Run this RSA legacy baseline; architecture and threat model agreed | Baseline results, diagram, issue list |
| 2 | Review baseline and propose tests/operations improvements | Findings, prompts, review decisions |
| 3 | ML-KEM proof of concept and critical code review | Crypto tests, review record, benchmarks |
| 4 | Gateway-cloud integration and explicit migration/fallback | Integration tests, migration decision record |
| 5 | Containerized test deployment and failure/security tests | Compose evidence, outage/tamper/replay results |
| 6 | Final measurements, technical debt review, five-page report | Tables, references, report draft |
| 7 | Peer-review changes, presentation, final validation | Peer-review response and release tag |

Suggested roles for a five-person group:

1. Sensor/legacy protocol and baseline measurements.
2. Gateway and migration logic.
3. Cloud storage and APIs.
4. ML-KEM integration and security testing.
5. CI/CD, containers, monitoring, and release evidence.

Each pull request should have a reviewer from another role. Rotate the roles after the first milestone so knowledge is shared.

For the maximum five-page group report, the best-fitting suggested perspective is **Information security and organizational cybersecurity**. It naturally connects crypto-agility, legacy constraints, AI governance, operational controls, and residual risk. The group must make the final choice and use referenced evidence. The individual 400-500 word reflection must be written without AI, as required by the brief.
