import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.session import get_db
from app.main import app


def test_create_organization_superadmin_success(client: TestClient):
    """Test that a superadmin CAN create organizations."""
    
    class MockSuperAdmin:
        id = "mock-superadmin"
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

    assert response.status_code == 201
    assert response.json()["name"] == "UNICEF Pilot"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


def test_create_organization_forbidden(client: TestClient):
    """Test that an org_admin CANNOT create organizations (403)."""
    
    # Mock Auth (Org Admin)
    class MockOrgAdmin:
        id = "mock-org-admin"
        email = "admin@unicef.org"
        role = "org_admin"
        
    app.dependency_overrides[get_current_user] = lambda: MockOrgAdmin()
    
    # Mock DB
    app.dependency_overrides[get_db] = lambda: MagicMock()

    payload = {"name": "Rogue Org", "is_active": True}
    response = client.post("/api/v1/organizations/", json=payload)

    assert response.status_code == 403
    assert "Only global SuperAdmins" in response.json()["detail"]

# ===========================================================================
# Atomic provisioning + soft-deactivate + hard-delete (real in-memory DB)
# ===========================================================================

from app.core.security import get_password_hash  # noqa: E402
from app.db.models import Organization, User, UserRole  # noqa: E402


def _seed_superadmin(db):
    """Create an HQ organization + a real superadmin bound to it."""
    hq = Organization(name="HQ Global", is_active=True)
    db.add(hq)
    db.flush()
    sa = User(
        email="sa@hq.org",
        full_name="Global Admin",
        hashed_password=get_password_hash("password123"),
        role=UserRole.superadmin,
        is_active=True,
        organization_id=hq.id,
    )
    db.add(sa)
    db.commit()
    db.refresh(sa)
    return sa


def test_create_organization_with_admin_atomic(client, db_session):
    """superadmin creates org + admin in one call; both persist, count == 1."""
    sa = _seed_superadmin(db_session)
    app.dependency_overrides[get_current_user] = lambda: sa

    payload = {
        "name": "Organización C",
        "is_active": True,
        "admin": {
            "full_name": "Administrador C",
            "email": "admin_c@organization.com",
            "password": "provisional123",
        },
    }
    resp = client.post("/api/v1/organizations/", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Organización C"
    assert body["user_count"] == 1
    assert body["patient_count"] == 0

    admin = db_session.query(User).filter(User.email == "admin_c@organization.com").first()
    assert admin is not None
    assert admin.role == UserRole.org_admin
    assert admin.organization_id == body["id"]


def test_create_organization_admin_duplicate_email_rolls_back(client, db_session):
    """If the admin email already exists, NOTHING is created (no orphan org)."""
    sa = _seed_superadmin(db_session)
    # Pre-existing user with the email we will try to reuse for the admin.
    db_session.add(
        User(
            email="taken@organization.com",
            full_name="Existing User",
            hashed_password=get_password_hash("password123"),
            role=UserRole.doctor,
            is_active=True,
            organization_id=sa.organization_id,
        )
    )
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: sa

    payload = {
        "name": "Ghost Org",
        "admin": {
            "full_name": "Would-be Admin",
            "email": "taken@organization.com",
            "password": "provisional123",
        },
    }
    resp = client.post("/api/v1/organizations/", json=payload)
    assert resp.status_code == 400, resp.text
    # The organization must NOT have been persisted.
    assert db_session.query(Organization).filter(Organization.name == "Ghost Org").first() is None


def test_deactivate_organization(client, db_session):
    sa = _seed_superadmin(db_session)
    org = Organization(name="To Deactivate", is_active=True)
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.patch(f"/api/v1/organizations/{org.id}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


def test_delete_empty_organization(client, db_session):
    sa = _seed_superadmin(db_session)
    org = Organization(name="Empty Org", is_active=True)
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.delete(f"/api/v1/organizations/{org.id}")
    assert resp.status_code == 204, resp.text
    assert db_session.query(Organization).filter(Organization.id == org.id).first() is None


def test_delete_nonempty_organization_conflict(client, db_session):
    sa = _seed_superadmin(db_session)
    org = Organization(name="Busy Org", is_active=True)
    db_session.add(org)
    db_session.flush()
    db_session.add(
        User(
            email="member@busy.org",
            full_name="Some Member",
            hashed_password=get_password_hash("password123"),
            role=UserRole.doctor,
            is_active=True,
            organization_id=org.id,
        )
    )
    db_session.commit()
    db_session.refresh(org)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.delete(f"/api/v1/organizations/{org.id}")
    assert resp.status_code == 409, resp.text
    assert db_session.query(Organization).filter(Organization.id == org.id).first() is not None


def test_delete_own_organization_forbidden(client, db_session):
    sa = _seed_superadmin(db_session)
    app.dependency_overrides[get_current_user] = lambda: sa
    resp = client.delete(f"/api/v1/organizations/{sa.organization_id}")
    assert resp.status_code == 403, resp.text


def test_delete_organization_forbidden_for_org_admin(client, db_session):
    org = Organization(name="Target Org", is_active=True)
    db_session.add(org)
    db_session.flush()
    admin = User(
        email="oa@target.org",
        full_name="Org Admin",
        hashed_password=get_password_hash("password123"),
        role=UserRole.org_admin,
        is_active=True,
        organization_id=org.id,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    app.dependency_overrides[get_current_user] = lambda: admin

    resp = client.delete(f"/api/v1/organizations/{org.id}")
    assert resp.status_code == 403, resp.text
