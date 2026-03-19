import uuid
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db

def test_create_user_org_admin_success(client: TestClient):
    """Test that an org_admin can create a doctor in their own organization."""
    
    class MockOrgAdmin:
        email = "admin@clinic.org"
        role = "org_admin"
        organization_id = "org-123"
        
    app.dependency_overrides[get_current_user] = lambda: MockOrgAdmin()

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    
    # Teach the mock to assign an ID when refresh() is called
    def mock_refresh(instance):
        instance.id = str(uuid.uuid4())
    mock_db.refresh.side_effect = mock_refresh
    
    app.dependency_overrides[get_db] = lambda: mock_db

    payload = {
        "email": "doctor@clinic.org",
        "full_name": "Dr. House",
        "password": "SecurePassword123!",
        "role": "doctor"
    }
    response = client.post("/api/v1/users/", json=payload)

    app.dependency_overrides.clear()
    
    assert response.status_code == 201
    assert response.json()["email"] == "doctor@clinic.org"


def test_create_user_org_admin_forbidden_role(client: TestClient):
    """Test that an org_admin CANNOT create another admin (privilege escalation)."""
    
    class MockOrgAdmin:
        email = "admin@clinic.org"
        role = "org_admin"
        organization_id = "org-123"
        
    app.dependency_overrides[get_current_user] = lambda: MockOrgAdmin()
    app.dependency_overrides[get_db] = lambda: MagicMock()

    payload = {
        "email": "hacker@clinic.org",
        "full_name": "Bad Actor",
        "password": "Password123!",
        "role": "org_admin" # Attempt to create an admin
    }
    response = client.post("/api/v1/users/", json=payload)

    app.dependency_overrides.clear()
    
    assert response.status_code == 403
    assert "can only create 'doctor' or 'nurse'" in response.json()["detail"]

def test_create_user_doctor_forbidden(client: TestClient):
    """Test that a doctor does NOT have access to user creation."""
    
    class MockDoctor:
        email = "doctor@clinic.org"
        role = "doctor"
        organization_id = "org-123"
        
    app.dependency_overrides[get_current_user] = lambda: MockDoctor()
    app.dependency_overrides[get_db] = lambda: MagicMock()

    payload = {
        "email": "nurse@clinic.org",
        "full_name": "Nurse Joy",
        "password": "Password123!",
        "role": "nurse"
    }
    response = client.post("/api/v1/users/", json=payload)

    app.dependency_overrides.clear()
    
    assert response.status_code == 403
    assert "Not enough privileges" in response.json()["detail"]