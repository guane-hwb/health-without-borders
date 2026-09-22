import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.models import UserRole
from app.db.session import get_db
from app.main import app


def test_create_user_org_admin_success(client: TestClient):
    """Test that an org_admin can create a doctor in their own organization."""
    
    class MockOrgAdmin:
        id = "mock-org-admin"
        email = "admin@clinic.org"
        role = UserRole.org_admin
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

    assert response.status_code == 201
    assert response.json()["email"] == "doctor@clinic.org"


def test_create_user_org_admin_forbidden_role(client: TestClient):
    """Test that an org_admin CANNOT create another admin (privilege escalation)."""
    
    class MockOrgAdmin:
        id = "mock-org-admin"
        email = "admin@clinic.org"
        role = UserRole.org_admin
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

    assert response.status_code == 403
    assert "can only create 'doctor' or 'nurse'" in response.json()["detail"]

def test_create_user_doctor_forbidden(client: TestClient):
    """Test that a doctor does NOT have access to user creation."""
    
    class MockDoctor:
        id = "mock-doctor"
        email = "doctor@clinic.org"
        role = UserRole.doctor
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

    assert response.status_code == 403
    assert "Not enough privileges" in response.json()["detail"]


def test_get_me_returns_current_user_profile(client: TestClient):
    """GET /me returns the authenticated user's own profile with correct fields."""

    class MockDoctor:
        id = "user-doc-001"
        email = "doctor@clinic.org"
        full_name = "Dr. House"
        role = UserRole.doctor
        organization_id = "org-123"
        is_active = True

    app.dependency_overrides[get_current_user] = lambda: MockDoctor()

    response = client.get("/api/v1/users/me")

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "doctor@clinic.org"
    assert data["full_name"] == "Dr. House"
    assert data["role"] == UserRole.doctor
    assert data["organization_id"] == "org-123"


def test_get_me_works_for_all_roles(client: TestClient):
    """GET /me is accessible to any authenticated role — no privilege check on this endpoint."""

    for role in [UserRole.org_admin, UserRole.nurse, UserRole.superadmin]:

        class MockUser:
            id = f"user-{role}-001"
            email = f"{role}@clinic.org"
            full_name = f"User {role}"
            organization_id = "org-123"
            is_active = True

        MockUser.role = role
        app.dependency_overrides[get_current_user] = lambda u=MockUser(): u

        response = client.get("/api/v1/users/me")

        assert response.status_code == 200, f"Expected 200 for role {role}, got {response.status_code}"
        assert response.json()["email"] == f"{role}@clinic.org"


# ===========================================================================
# Soft-deactivate + hard-delete users with guards (real in-memory DB)
# ===========================================================================

from app.core.security import get_password_hash  # noqa: E402
from app.db.models import Organization, User  # noqa: E402


def _seed_org(db, name="Clinic"):
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.flush()
    return org


def _mk_user(db, org_id, email, role, name="User X"):
    u = User(
        email=email,
        full_name=name,
        hashed_password=get_password_hash("password123"),
        role=role,
        is_active=True,
        organization_id=org_id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_deactivate_user_org_admin(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: admin

    resp = client.patch(f"/api/v1/users/{doctor.id}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


def test_delete_user_org_admin_doctor(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: admin

    resp = client.delete(f"/api/v1/users/{doctor.id}")
    assert resp.status_code == 204, resp.text
    assert db_session.query(User).filter(User.id == doctor.id).first() is None


def test_delete_user_self_forbidden(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    app.dependency_overrides[get_current_user] = lambda: admin

    resp = client.delete(f"/api/v1/users/{admin.id}")
    assert resp.status_code == 400, resp.text


def test_delete_user_cross_org_forbidden(client, db_session):
    org_a = _seed_org(db_session, "Org A")
    admin_a = _mk_user(db_session, org_a.id, "admin@a.org", UserRole.org_admin)
    org_b = _seed_org(db_session, "Org B")
    doctor_b = _mk_user(db_session, org_b.id, "doc@b.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: admin_a

    resp = client.delete(f"/api/v1/users/{doctor_b.id}")
    assert resp.status_code == 403, resp.text
    assert db_session.query(User).filter(User.id == doctor_b.id).first() is not None


def test_delete_last_org_admin_conflict(client, db_session):
    """A superadmin cannot strip an org of its only administrator."""
    hq = _seed_org(db_session, "HQ")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    org = _seed_org(db_session, "Solo Org")
    only_admin = _mk_user(db_session, org.id, "solo@org.org", UserRole.org_admin)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.delete(f"/api/v1/users/{only_admin.id}")
    assert resp.status_code == 409, resp.text
    assert db_session.query(User).filter(User.id == only_admin.id).first() is not None


def test_delete_org_admin_with_backup(client, db_session):
    """Deleting an org_admin is allowed when another admin remains."""
    hq = _seed_org(db_session, "HQ")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    org = _seed_org(db_session, "Duo Org")
    admin1 = _mk_user(db_session, org.id, "a1@org.org", UserRole.org_admin)
    _mk_user(db_session, org.id, "a2@org.org", UserRole.org_admin)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.delete(f"/api/v1/users/{admin1.id}")
    assert resp.status_code == 204, resp.text


def test_delete_superadmin_forbidden(client, db_session):
    hq = _seed_org(db_session, "HQ")
    sa1 = _mk_user(db_session, hq.id, "sa1@hq.org", UserRole.superadmin)
    sa2 = _mk_user(db_session, hq.id, "sa2@hq.org", UserRole.superadmin)
    app.dependency_overrides[get_current_user] = lambda: sa1

    resp = client.delete(f"/api/v1/users/{sa2.id}")
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# POST /users/ — superadmin tenancy + duplicate email
# ---------------------------------------------------------------------------


def _new_user_payload(email="new@clinic.org", role="doctor", **extra):
    return {
        "email": email,
        "full_name": "New Person",
        "password": "SecurePassword123!",
        "role": role,
        **extra,
    }


def test_create_user_duplicate_email_returns_400(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _mk_user(db_session, org.id, "taken@clinic.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: admin

    resp = client.post("/api/v1/users/", json=_new_user_payload("taken@clinic.org"))
    assert resp.status_code == 400, resp.text
    assert "already exists" in resp.json()["detail"]


def test_create_user_superadmin_requires_organization_id(client, db_session):
    hq = _seed_org(db_session, "HQ")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.post("/api/v1/users/", json=_new_user_payload(role="org_admin"))
    assert resp.status_code == 400, resp.text
    assert "organization_id" in resp.json()["detail"]
    assert db_session.query(User).filter(User.email == "new@clinic.org").first() is None


def test_create_user_superadmin_in_target_organization(client, db_session):
    hq = _seed_org(db_session, "HQ")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    target_id = _seed_org(db_session, "Target Clinic").id
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.post(
        "/api/v1/users/",
        json=_new_user_payload(role="org_admin", organization_id=target_id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["organization_id"] == target_id
    assert body["role"] == "org_admin"


# ---------------------------------------------------------------------------
# GET /users/ — role-scoped listing
# ---------------------------------------------------------------------------


def test_list_users_superadmin_sees_all_organizations(client, db_session):
    hq = _seed_org(db_session, "HQ")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    org_a = _seed_org(db_session, "Org A")
    _mk_user(db_session, org_a.id, "doc@a.org", UserRole.doctor)
    org_b = _seed_org(db_session, "Org B")
    _mk_user(db_session, org_b.id, "nurse@b.org", UserRole.nurse)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.get("/api/v1/users/")
    assert resp.status_code == 200, resp.text
    assert {u["email"] for u in resp.json()} == {"sa@hq.org", "doc@a.org", "nurse@b.org"}


def test_list_users_org_admin_sees_only_own_organization(client, db_session):
    org_a = _seed_org(db_session, "Org A")
    admin_a = _mk_user(db_session, org_a.id, "admin@a.org", UserRole.org_admin)
    _mk_user(db_session, org_a.id, "doc@a.org", UserRole.doctor)
    org_b = _seed_org(db_session, "Org B")
    _mk_user(db_session, org_b.id, "doc@b.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: admin_a

    resp = client.get("/api/v1/users/")
    assert resp.status_code == 200, resp.text
    assert {u["email"] for u in resp.json()} == {"admin@a.org", "doc@a.org"}


def test_list_users_forbidden_for_clinical_roles(client, db_session):
    org = _seed_org(db_session)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: doctor

    resp = client.get("/api/v1/users/")
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# PATCH / DELETE /users/{id} — shared management guards
# ---------------------------------------------------------------------------


def test_manage_user_forbidden_for_clinical_roles(client, db_session):
    org = _seed_org(db_session)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    nurse = _mk_user(db_session, org.id, "nurse@clinic.org", UserRole.nurse)
    app.dependency_overrides[get_current_user] = lambda: doctor

    resp = client.patch(f"/api/v1/users/{nurse.id}", json={"is_active": False})
    assert resp.status_code == 403, resp.text


def test_manage_user_not_found(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    app.dependency_overrides[get_current_user] = lambda: admin

    resp = client.delete("/api/v1/users/does-not-exist")
    assert resp.status_code == 404, resp.text


def test_org_admin_cannot_manage_another_org_admin(client, db_session):
    org = _seed_org(db_session)
    admin1 = _mk_user(db_session, org.id, "a1@clinic.org", UserRole.org_admin)
    admin2 = _mk_user(db_session, org.id, "a2@clinic.org", UserRole.org_admin)
    app.dependency_overrides[get_current_user] = lambda: admin1

    resp = client.patch(f"/api/v1/users/{admin2.id}", json={"is_active": False})
    assert resp.status_code == 403, resp.text
    assert "doctor' or 'nurse'" in resp.json()["detail"]
