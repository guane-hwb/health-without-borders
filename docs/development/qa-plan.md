# Quality Assurance (QA) and Pull Request Workflow

This document outlines the Quality Assurance (QA) strategy, testing methodologies, data structures, and the required Pull Request (PR) workflow for the Health Without Borders backend. It is designed to ensure code reliability, security, and maintainability for all open-source contributors.

---

## 1. Testing Strategy

Our backend relies on a robust automated testing strategy using `pytest`. The strategy is divided into three main layers:

* **Unit Testing:** Focuses on isolated business logic, Pydantic schema validations, and utility functions (e.g., LLM parsing, HL7 generation).
* **Integration Testing:** Tests the FastAPI endpoints and their interaction with the PostgreSQL database using a dedicated test database and rolled-back transactions.
* **Coverage Goals:** We are committed to progressively increasing our test coverage as part of our Open Source roadmap:
  * Milestone 1 (Q2): 15% code coverage.
  * Milestone 2 (Q3): 45% code coverage.
  * Milestone 3 (Q4): 80% code coverage.

### Running Tests Locally
To run the test suite, use our package manager `uv`:
```bash
uv run pytest --cov=app tests/

```

---

## 2. Data Structures and Schemas

Our application strictly enforces data validation using **Pydantic** (V2) schemas. This ensures that any data entering or leaving the API is strongly typed and validated.

**Key Data Structures:**

* **PatientRecord:** Contains demographic data, linked to HL7 constraints.
* **DiagnosisItem:** Enforces WHO ICD-10 and ICD-11 coding standards.
* **ClinicalEvaluation:** Handles textual data for AI processing.

All incoming JSON payloads are parsed into these Pydantic models before interacting with the SQLAlchemy ORM layer.

---

## 3. Sample Test Case

Below is an example of an integration test for our Patient endpoint. It demonstrates how we use the FastAPI `TestClient` alongside our Pydantic data structures to validate the creation of a new medical record.

```python
# tests/api/v1/test_patients.py

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_create_patient_success(db_session):
    payload = {
        "patientId": "123e4567-e89b-12d3-a456-426614174000",
        "device_uid": "NFC-9988",
        "patientInfo": {
            "firstName": "Ana",
            "lastName": "Perez",
            "dob": "2015-05-15",
            "gender": "F",
            "bloodType": "O+"
        }
    }
    
    response = client.post(
        "/api/v1/patients/sync",
        json=payload,
        headers={"Authorization": "Bearer test_token"}
    )
    
    assert response.status_code == 201
    assert response.json()["patientId"] == payload["patientId"]
    assert "sync_timestamp" in response.json()

```

---

## 4. Pull Request (PR) Workflow

To maintain code quality and stability, all contributors must follow this workflow when submitting code:

### Step 1: Branch Naming Convention

Create a new branch from `main` using the following prefixes:

* `feat/` for new features (e.g., `feat/llm-icd11-mapping`)
* `fix/` for bug fixes (e.g., `fix/jwt-expiration`)
* `docs/` for documentation updates
* `test/` for adding or updating tests

### Step 2: Local QA Checks

Before pushing your branch, you must ensure:

1. All local tests pass (`uv run pytest`).
2. Code follows PEP-8 styling.

### Step 3: Opening the Pull Request

* Open the PR against the `develop` branch (our integration and staging branch).
* Provide a clear description of the changes. If the PR resolves an open issue, link it (e.g., "Closes #12").

### Step 4: Code Review, Merge and CI/CD

* **Continuous Integration (CI):** Once opened, automated CI checks (via GitHub Actions / Google Cloud Build) will run the test suite and coverage reports. The CI must pass successfully.
* **Peer Review:** At least one core maintainer must review and approve the PR.
* **Merge to Develop:** Once approved and all checks pass, the maintainer will squash and merge the PR into `develop`. This action automatically triggers the continuous deployment pipeline to our staging environment on Google Cloud Run.
* **Production Releases:** Periodically, the `develop` branch will be merged into the `main` branch to create stable production releases.

