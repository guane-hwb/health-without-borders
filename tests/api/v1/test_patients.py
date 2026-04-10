from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user
from app.db.models import UserRole


class MockUser:
    email = "doctor_test@hwb.org"
    id = "test-user-id"
    role = UserRole.doctor
    organization_id = "org-123"


class MockNurse:
    email = "nurse_test@hwb.org"
    id = "test-nurse-id"
    role = UserRole.nurse
    organization_id = "org-123"


class MockUnauthorized:
    email = "viewer@hwb.org"
    id = "test-viewer-id"
    role = UserRole.org_admin  # org_admin can search but not sync
    organization_id = "org-123"


# RDA-compliant mock payload (Resolution 1888/2025)
MOCK_PATIENT_PAYLOAD = {
    "patientId": "TEST-UNIT-001",
    "device_uid": "04:A2:TEST:UID",
    "patientInfo": {
        "identification": {
            "documentType": "PT",
            "documentNumber": "VZ-9876543",
        },
        "firstLastName": "Rodríguez",
        "secondLastName": "Pérez",
        "firstName": "Santiago",
        "secondName": "Andrés",
        "dob": "2020-01-01",
        "nationalityCode": "VEN",
        "nationalityName": "Venezuela",
        "biologicalSex": "M",
        "address": {
            "street": "Calle 10 # 5-20",
            "city": "Cúcuta",
            "cityCode": "54001",
            "state": "Norte de Santander",
            "zipCode": "540001",
            "country": "COL",
            "countryName": "Colombia",
            "zone": "U",
        },
        "bloodType": "O+",
        "weight": 15.5,
        "height": 100.0,
    },
    "guardianInfo": {
        "name": "María Pérez",
        "relationship": "Madre",
        "phone": "+573001234567",
        "device_uid": "GUARDIAN-UID-001",
    },
    "backgroundHistory": {
        "chronicConditions": None,
        "personalHistory": None,
        "familyHistory": [
            {
                "conditionCie10Code": "E11",
                "conditionDescription": "Diabetes mellitus tipo 2",
                "relationship": "04",
            }
        ],
        "familyHistoryNotes": "Abuelo paterno con diabetes.",
    },
    "allergies": [
        {
            "category": "01",
            "allergen": "Penicilina",
            "reaction": "Habones",
            "notes": "Reacción leve en la infancia reportada por la madre",
        }
    ],
    "medicalHistory": [],
    "vaccinationRecord": [],
}

# Payload with a medical visit
MOCK_PATIENT_WITH_VISIT = {
    **MOCK_PATIENT_PAYLOAD,
    "patientId": "TEST-UNIT-002",
    "device_uid": "04:A2:TEST:UID2",
    "patientInfo": {
        **MOCK_PATIENT_PAYLOAD["patientInfo"],
        "identification": {
            "documentType": "PT",
            "documentNumber": "VZ-1111111",
        },
    },
    "medicalHistory": [
        {
            "type": "Consultation",
            "startDateTime": "2026-01-15T09:00:00",
            "endDateTime": "2026-01-15T09:45:00",
            "careModality": "01",
            "serviceGroup": "01",
            "careEnvironment": "05",
            "provider": {
                "repsCode": "540015400101",
                "name": "Hospital Erasmo Meoz",
            },
            "practitioner": {
                "documentType": "CC",
                "documentNumber": "88001234",
                "name": "GOMEZ, ANDREA",
            },
            "location": "HOSPITAL_ERASMO_MEOZ",
            "physician": "GOMEZ, ANDREA",
            "clinicalEvaluation": {
                "historyOfCurrentIllness": "Fiebre de 3 días de evolución, tos seca.",
                "generalPhysicalExamination": "T: 38.5°C, FC: 110. Faringe eritematosa.",
                "systemsExamination": "Respiratorio: murmullo vesicular conservado.",
                "treatmentPlanObservations": "Acetaminofén 15mg/kg cada 6h. Control en 48h.",
            },
            "diagnosis": [
                {
                    "icd10Code": "J06.9",
                    "description": "Infección aguda de las vías respiratorias superiores",
                }
            ],
            "diagnosisType": "01",
            "riskFactors": [],
            "incapacity": None,
            "payer": None,
        }
    ],
}


def _override_doctor():
    app.dependency_overrides[get_current_user] = lambda: MockUser()


def _override_nurse():
    app.dependency_overrides[get_current_user] = lambda: MockNurse()


def _clear_overrides():
    app.dependency_overrides.pop(get_current_user, None)


def _sync_patient(client, payload=None):
    """Helper: sync a patient as doctor with GCP mocked."""
    _override_doctor()
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        return client.post("/api/v1/patients/sync", json=payload or MOCK_PATIENT_PAYLOAD)


# ============================================================================
# SYNC ENDPOINT TESTS
# ============================================================================


def test_sync_patient_success(client: TestClient):
    """Sync patient with RDA-compliant payload returns 201 and correct data."""
    response = _sync_patient(client)
    _clear_overrides()

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "success"
    assert data["internal_id"] == "TEST-UNIT-001"
    assert data["gcp_status"] == "success"


def test_sync_patient_with_visit_generates_multiple_bundles(client: TestClient):
    """Syncing with 1 visit generates 2 FHIR bundles (RDA-Paciente + RDA-Consulta)."""
    _override_doctor()
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_WITH_VISIT)
        _clear_overrides()

        assert response.status_code == 201
        assert mock_gcp.call_count == 2


def test_sync_nurse_cannot_add_medical_history(client: TestClient):
    """A nurse cannot add medical history entries to an existing patient."""
    _sync_patient(client)
    _clear_overrides()

    _override_nurse()
    payload_with_visit = {
        **MOCK_PATIENT_PAYLOAD,
        "medicalHistory": [
            {
                "type": "Consultation",
                "startDateTime": "2026-02-01T10:00:00",
                "careModality": "01",
                "serviceGroup": "01",
                "careEnvironment": "05",
                "clinicalEvaluation": {"historyOfCurrentIllness": "Fiebre"},
                "diagnosis": [],
            }
        ],
    }
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        response = client.post("/api/v1/patients/sync", json=payload_with_visit)

    _clear_overrides()
    assert response.status_code == 403
    assert "Nurses can only add vaccines" in response.json()["detail"]


def test_sync_stores_rda_columns_in_db(client: TestClient, db_session):
    """New RDA columns are persisted correctly in the database."""
    from app.db.models import Patient

    _sync_patient(client)
    _clear_overrides()

    patient = db_session.query(Patient).filter(Patient.id == "TEST-UNIT-001").first()
    assert patient is not None
    assert patient.document_type == "PT"
    assert patient.document_number == "VZ-9876543"
    assert patient.nationality_code == "VEN"
    assert patient.biological_sex == "M"
    assert patient.last_name == "Rodríguez"
    assert patient.second_last_name == "Pérez"
    assert patient.first_name == "Santiago"


# ============================================================================
# SCAN ENDPOINT TESTS
# ============================================================================


def test_scan_success(client: TestClient):
    """Scanning a registered device with guardian UID returns the patient record."""
    _sync_patient(client)

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}",
        params={"guardian_device_uid": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]},
    )
    _clear_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == "TEST-UNIT-001"
    assert data["patientInfo"]["firstName"] == "Santiago"
    assert data["patientInfo"]["identification"]["documentType"] == "PT"
    assert data["patientInfo"]["nationalityCode"] == "VEN"


def test_scan_not_found(client: TestClient):
    """Scanning an unregistered device returns 404."""
    _override_doctor()
    response = client.get("/api/v1/patients/scan/NONEXISTENT-UID")
    _clear_overrides()

    assert response.status_code == 404
    assert "not registered" in response.json()["detail"]


def test_scan_minor_requires_guardian(client: TestClient):
    """Scanning a minor's bracelet without guardian UID returns 403."""
    _sync_patient(client)

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}"
    )
    _clear_overrides()

    assert response.status_code == 403
    assert "Guardian bracelet scan required" in response.json()["detail"]


def test_scan_minor_wrong_guardian(client: TestClient):
    """Scanning a minor's bracelet with a wrong guardian UID returns 403."""
    _sync_patient(client)

    response = client.get(
        f"/api/v1/patients/scan/{MOCK_PATIENT_PAYLOAD['device_uid']}",
        params={"guardian_device_uid": "WRONG-GUARDIAN-UID"},
    )
    _clear_overrides()

    assert response.status_code == 403
    assert "Guardian tag mismatch" in response.json()["detail"]


# ============================================================================
# SEARCH (STRICT LOOKUP) ENDPOINT TESTS
# ============================================================================


def test_search_exact_match_returns_patient(client: TestClient):
    """Strict lookup with all correct fields returns the single patient."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == "TEST-UNIT-001"
    assert data["patientInfo"]["identification"]["documentNumber"] == "VZ-9876543"


def test_search_by_second_last_name(client: TestClient):
    """Lookup using the second last name also finds the patient."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Pérez",  # second last name
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-001"


def test_search_case_insensitive(client: TestClient):
    """Lookup is case-insensitive for names and document number."""
    _sync_patient(client)

    params = {
        "document_number": "vz-9876543",   # lowercase
        "birth_date": "2020-01-01",
        "first_name": "santiago",           # lowercase
        "last_name": "rodríguez",           # lowercase
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-001"


def test_search_with_guardian_name(client: TestClient):
    """Providing guardian_name adds extra verification and still finds the patient."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
        "guardian_name": "María",  # partial match
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 200
    assert response.json()["patientId"] == "TEST-UNIT-001"


def test_search_wrong_document_returns_404(client: TestClient):
    """Wrong document number returns 404 — no data leaked."""
    _sync_patient(client)

    params = {
        "document_number": "WRONG-DOC-999",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 404
    assert "No patient found" in response.json()["detail"]


def test_search_wrong_name_returns_404(client: TestClient):
    """Correct document but wrong first name returns 404."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Carlos",  # wrong name
        "last_name": "Rodríguez",
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 404


def test_search_wrong_dob_returns_404(client: TestClient):
    """Correct document and name but wrong DOB returns 404."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "1999-12-31",  # wrong DOB
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 404


def test_search_wrong_guardian_returns_404(client: TestClient):
    """Correct data but wrong guardian name returns 404."""
    _sync_patient(client)

    params = {
        "document_number": "VZ-9876543",
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
        "guardian_name": "Pedro González",  # wrong guardian
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 404


def test_search_missing_mandatory_params_returns_422(client: TestClient):
    """Missing mandatory parameters triggers FastAPI validation (422)."""
    _override_doctor()

    # Missing document_number (mandatory)
    params = {
        "birth_date": "2020-01-01",
        "first_name": "Santiago",
        "last_name": "Rodríguez",
    }
    response = client.get("/api/v1/patients/search", params=params)
    _clear_overrides()

    assert response.status_code == 422