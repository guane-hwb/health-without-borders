# tests/api/v1/test_patients.py
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user

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
    
    class MockUser:
        email = "doctor_prueba@hwb.org"
        id = 1
        
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # --- MOCKING ---
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        
        # Configure the mock to return a success dictionary
        mock_gcp.return_value = {
            "status": "success", 
            "google_response": {"messageId": "mock-gcp-id"}
        }

        # --- ACTION ---
        # Enviamos el payload actualizado
        response = client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

        app.dependency_overrides.pop(get_current_user, None)

        # --- ASSERTION ---
        assert response.status_code == 201
        
        data = response.json()
        assert data["status"] == "success"
        assert data["internal_id"] == "TEST-UNIT-001"
        assert data["gcp_status"] == "success"

        # Verify that the system actually attempted to send data to GCP
        mock_gcp.assert_called_once()



# --- PRUEBAS PARA EL ENDPOINT DE ESCANEO (/scan) ---
def test_get_patient_by_device_success(client: TestClient):
    """Prueba que el escaneo de un dispositivo válido retorna al paciente."""
    
    # 1. Burlamos la seguridad (Auth Mock)
    class MockUser:
        email = "doctor_prueba@hwb.org"
        id = 1
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # 2. Insertamos al paciente primero usando el endpoint de sync (burlamos GCP)
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

    # 3. Acción: Simulamos el escaneo de la manilla
    device_uid = MOCK_PATIENT_PAYLOAD["device_uid"]
    response = client.get(f"/api/v1/patients/scan/{device_uid}")

    # 4. Limpieza y Aserciones
    app.dependency_overrides.pop(get_current_user, None)
    
    assert response.status_code == 200
    data = response.json()
    assert data["patientId"] == MOCK_PATIENT_PAYLOAD["patientId"]
    assert data["patientInfo"]["firstName"] == "User"

def test_get_patient_by_device_not_found(client: TestClient):
    """Prueba que escanear una manilla no registrada devuelve 404."""
    
    class MockUser:
        email = "doctor_prueba@hwb.org"
        id = 1
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # Acción: Escaneamos un ID inventado
    response = client.get("/api/v1/patients/scan/ID-FALSO-999")
    app.dependency_overrides.pop(get_current_user, None)

    # Aserción
    assert response.status_code == 404
    assert "not registered" in response.json()["detail"]


# --- PRUEBAS PARA EL ENDPOINT DE BÚSQUEDA (/search) ---
def test_search_patients_success(client: TestClient):
    """Prueba que la búsqueda avanzada retorna resultados si coinciden los datos obligatorios."""
    
    class MockUser:
        email = "doctor_prueba@hwb.org"
        id = 1
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # 1. Insertamos al paciente
    with patch("app.api.v1.endpoints.patients.send_to_google_healthcare") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        client.post("/api/v1/patients/sync", json=MOCK_PATIENT_PAYLOAD)

    # 2. Acción: Buscamos con los parámetros exactos
    params = {
        "first_name": "User",
        "last_name": "Test",
        "birth_date": "2020-01-01"
    }
    response = client.get("/api/v1/patients/search", params=params)
    
    app.dependency_overrides.pop(get_current_user, None)

    # 3. Aserciones
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["patientId"] == MOCK_PATIENT_PAYLOAD["patientId"]

def test_search_patients_missing_mandatory_params(client: TestClient):
    """Prueba que si falta un parámetro obligatorio (ej. fecha de nacimiento), FastAPI bloquea la petición (422)."""
    
    class MockUser:
        email = "doctor_prueba@hwb.org"
        id = 1
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    # Acción: Buscamos SOLO por nombre y apellido, omitiendo birth_date
    params = {
        "first_name": "User",
        "last_name": "Test"
    }
    response = client.get("/api/v1/patients/search", params=params)
    
    app.dependency_overrides.pop(get_current_user, None)

    # Aserción: FastAPI debe retornar 422 Unprocessable Entity
    assert response.status_code == 422