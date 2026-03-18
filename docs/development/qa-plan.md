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

Below is an example of an integration test for our Patient endpoint. It demonstrates how we use the FastAPI `TestClient`, dependency overrides for RBAC authentication, and `unittest.mock` to safely test the creation of a new medical record without hitting the production Google Cloud API.

```python
# tests/api/v1/test_patients.py

from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user

client = TestClient(app)

def test_sync_patient_success():
    # 1. Mock RBAC Authentication
    class MockUser:
        email = "doctor_prueba@hwb.org"
        id = 1
        role = "doctor"
        organization_id = "org-123"
        
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # 2. Valid Pydantic Payload
    payload = {
        "patientId": "TEST-UNIT-001",
        "device_uid": "04:A2:TEST:UID",
        "patientInfo": {
            "lastName": "Test",
            "firstName": "User",
            "dob": "2020-01-01",
            "gender": "M",
            "bloodType": "O+",
            "address": {
                "street": "Test", "city": "Cucuta", "state": "NS", 
                "zipCode": "540001", "country": "COL"
            }
        },
        "guardianInfo": {
            "name": "Mom Test",
            "relationship": "Mother",
            "phone": "123456789"
        },
        "allergies": [],
        "medicalHistory": [],
        "vaccinationRecord": []
    }
    
    # 3. Mock External GCP Service & Execute
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        
        response = client.post("/api/v1/patients/sync", json=payload)
        
        # Cleanup
        app.dependency_overrides.pop(get_current_user, None)
        
        # 4. Assertions (Matching PatientSyncResponse Schema)
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "success"
        assert data["internal_id"] == "TEST-UNIT-001"
        assert data["gcp_status"] == "success"
        
        mock_gcp.assert_called_once()

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

---

## 5. Remediation Program Execution Model

The active remediation effort (security, quality, functional consistency, and ISO 27001 technical evidence) is executed with strict traceability:

- Working branch: `feature/quality-compliance-test`
- Target branch: `develop`
- Delivery rule: **one phase equals one commit**
- Review rule: **one PR per phase**

Reference document:

- [docs/development/remediation-program.md](docs/development/remediation-program.md)

### Required evidence per phase

Every phase must include:

1. Clear risk statement and scope.
2. Technical change summary.
3. Validation evidence (tests/checks run).
4. Rollback instructions.
5. Explicit impact statement for deployment.

