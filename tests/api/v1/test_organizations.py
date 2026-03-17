import uuid
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db


def test_create_organization_superadmin_success(client: TestClient):
    """Test that a superadmin CAN create organizations."""
    
    class MockSuperAdmin:
        email = "boss@global.org"
        role = "superadmin"
        
    app.dependency_overrides[get_current_user] = lambda: MockSuperAdmin()

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    
    # Teach the mock to assign an ID when refresh() is called
    def mock_refresh(instance):
        instance.id = str(uuid.uuid4())
    mock_db.refresh.side_effect = mock_refresh
    
    app.dependency_overrides[get_db] = lambda: mock_db

    payload = {"name": "UNICEF Pilot", "is_active": True}
    response = client.post("/api/v1/organizations/", json=payload)

    app.dependency_overrides.clear()
    
    assert response.status_code == 201
    assert response.json()["name"] == "UNICEF Pilot"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


def test_create_organization_forbidden(client: TestClient):
    """Test that an org_admin CANNOT create organizations (403)."""
    
    # Mock Auth (Org Admin)
    class MockOrgAdmin:
        email = "admin@unicef.org"
        role = "org_admin"
        
    app.dependency_overrides[get_current_user] = lambda: MockOrgAdmin()
    
    # Mock DB
    app.dependency_overrides[get_db] = lambda: MagicMock()

    payload = {"name": "Rogue Org", "is_active": True}
    response = client.post("/api/v1/organizations/", json=payload)

    app.dependency_overrides.clear()
    
    assert response.status_code == 403
    assert "Only global SuperAdmins" in response.json()["detail"]