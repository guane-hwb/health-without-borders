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

# Mock Payload simulating a request from the Mobile App (Frontend)
MOCK_PATIENT_PAYLOAD = {
  "patientId": "TEST-UNIT-001",
  "device_uid": "04:A2:TEST:UID",
  "patientInfo": {
    "lastName": "Test",
    "firstName": "User",
    "dob": "2020-01-01",
    "gender": "M",
    "bloodType": "O+",
    "weight": 15.5,
    "height": 100.0,
    "address": {
      "street": "Test St",
      "city": "Cucuta",
      "state": "NS",
      "zipCode": "540001",
      "country": "COL"
    }
  },
  "guardianInfo": {
    "name": "Mom Test",
    "relationship": "Mother",
    "phone": "123456789",
    "device_uid": "GUARDIAN-UID-001"
  },
  "allergies": [
      {
          "allergen": "Penicilina",
          "reaction": "Habones",
          "notes": "Reacción leve en la infancia reportada por la madre"
      }
  ],
  "medicalHistory": [],
  "vaccinationRecord": []
}

def test_sync_patient_success(client: TestClient):
    """
    Integration Test: Sync Patient Data (Success Scenario)
    Verifies that the API accepts the mobile payload and successfully triggers
    the internal FHIR RDA conversion and GCP Healthcare transmission.
    """
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # --- MOCKING ---
    # We mock the GCP service to prevent actual network calls to the FHIR Store during testing
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        
        # Configure the mock to return a success dictionary
        mock_gcp.return_value = {
            "status": "success", 
            "google_response": {"messageId": "mock-gcp-fhir-id"}
        }

        # --- ACTION ---
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

        app.dependency_overrides.pop(get_current_user, None)

        # --- ASSERTION ---
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "success"
        assert data["internal_id"] == "TEST-UNIT-001"
        assert data["gcp_status"] == "success"

        mock_gcp.assert_called_once()


# --- TEST FOR THE SCAN ENDPOINT (/scan) ---
def test_get_patient_by_device_success(client: TestClient):
    """Test that scanning a registered device UID returns the correct patient record."""
    
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

    device_uid = MOCK_PATIENT_PAYLOAD["device_uid"]
    guardian_device_uid = MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]
    response = client.get(
        f"/api/v1/patients/scan/{device_uid}",
        params={"guardian_device_uid": guardian_device_uid}
    )

    app.dependency_overrides.pop(get_current_user, None)
    
    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == MOCK_PATIENT_PAYLOAD["patientId"]
    assert data["patientInfo"]["firstName"] == "User"

def test_get_patient_by_device_not_found(client: TestClient):
    """Test that scanning an unregistered device UID returns 404 Not Found."""
    
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    response = client.get("/api/v1/patients/scan/ID-FALSO-999")
    app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 404
    assert "not registered" in response.json()["detail"]


# --- TEST FOR THE SEARCH ENDPOINT (/search) ---
def test_search_patients_success(client: TestClient):
    """Test that advanced search returns results if mandatory data matches."""
    
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

    params = {
        "first_name": "User",
        "last_name": "Test",
        "birth_date": "2020-01-01"
    }
    response = client.get("/api/v1/patients/search", params=params)
    
    app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["patientId"] == MOCK_PATIENT_PAYLOAD["patientId"]

def test_search_patients_missing_mandatory_params(client: TestClient):
    """Test that if a mandatory parameter is missing (e.g., birth date), FastAPI blocks the request (422)."""
    
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    params = {
        "first_name": "User",
        "last_name": "Test"
    }
    response = client.get("/api/v1/patients/search", params=params)
    
    app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422