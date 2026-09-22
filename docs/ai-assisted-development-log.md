# AI-Assisted Development Log

## Task 1 - Create the Project Baseline

**Prompt:** Analyze the requirements in `SDMO_Project.pdf` and `Group work project 1 pdf.pdf`, then develop the initial Python-based project without relying on lecturer-provided starter code. The system should simulate a temperature sensor that transmits readings to an edge gateway, which then forwards the data to a cloud service.

**Output:** Created a Python repository containing a simulated temperature sensor, FastAPI edge gateway, FastAPI cloud service, SQLite storage, Docker configuration, initial automated tests, a CI workflow, and supporting project documentation.

## Task 2 - Keep the Initial System Incomplete

**Prompt:** Revise the initial implementation so that it is functional but intentionally incomplete, reflecting the legacy-system conditions described in the project brief. It should have limited test coverage, an unfinished CI/CD pipeline, insufficient monitoring, and an outdated key-establishment mechanism. These limitations will be evaluated and improved through later AI-assisted maintenance tasks.

**Output:** Reworked the repository into a legacy baseline using RSA-2048 OAEP key transport with AES-256-GCM instead of ML-KEM. Limited the project to two small tests, a CI workflow that only runs tests, basic console logging, and simple liveness endpoints. Both tests passed, and a local end-to-end smoke test successfully stored a sensor reading. Docker Compose configuration was validated, but the image build could not be tested because Docker Desktop was not running.

## Task 3 - Record Prompts and Outputs

**Prompt:** After completing each task, record a concise, professionally written version of the prompt and a summary of the corresponding output for later use in the project report.

**Output:** Added a chronological prompt-and-output record to the AI-assisted development log and established the same format for future tasks.

## Task 4 - Professionalize the Prompt Records

**Prompt:** Rewrite the prompts in the AI-assisted development log using clear, formal, and report-ready language while preserving their original technical meaning.

**Output:** Rewrote the recorded prompts in professional language without changing their intended requirements.
