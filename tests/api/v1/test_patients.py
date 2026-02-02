# tests/api/v1/test_patients.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Mock Payload simulating a request from the Mobile App (Frontend)
MOCK_PATIENT_PAYLOAD = {
  "patientId": "TEST-UNIT-001",
  "nfc_uid": "04:A2:TEST:UID",
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
    "phone": "123456789"
  },
  "medicalHistory": [],
  "vaccinationRecord": []
}

def test_sync_patient_success(client: TestClient):
    """
    Integration Test: Sync Patient Data (Success Scenario)
    
    Scenario:
        - Valid patient JSON is sent to the backend.
        - Google Cloud Healthcare API is available (simulated via Mock).
        
    Expected Outcome:
        1. HTTP 201 Created status.
        2. Response body contains 'status': 'success'.
        3. The internal ID matches the sent ID.
        4. The Google Cloud service function is called exactly once.
    """
    
    # --- MOCKING ---
    # We patch the 'send_to_google_healthcare' function to prevent actual network calls.
    # This simulates a successful response from the Google Cloud Service.
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        
        # Configure the mock to return a success dictionary
        mock_gcp.return_value = {
            "status": "success", 
            "google_response": {"messageId": "mock-gcp-id"}
        }

        # --- ACTION ---
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

        # --- ASSERTION ---
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "success"
        assert data["internal_id"] == "TEST-UNIT-001"
        assert data["gcp_status"] == "success"

        # Verify that the system actually attempted to send data to GCP
        mock_gcp.assert_called_once()