import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_current_user_allowing_temporary_password
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
        must_change_password = True
        document_type = None
        document_number = None

    app.dependency_overrides[get_current_user_allowing_temporary_password] = lambda: MockDoctor()

    response = client.get("/api/v1/users/me")

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "doctor@clinic.org"
    assert data["full_name"] == "Dr. House"
    assert data["role"] == UserRole.doctor
    assert data["organization_id"] == "org-123"
    assert data["must_change_password"] is True


def test_get_me_without_a_stored_name_shows_the_email(client: TestClient):
    """full_name is nullable in the table. No creation path leaves it empty,
    but a row edited by hand must not turn /me into a 500."""

    class MockDoctor:
        id = "user-doc-002"
        email = "noname@clinic.org"
        full_name = None
        role = UserRole.doctor
        organization_id = "org-123"
        is_active = True
        must_change_password = False
        document_type = None
        document_number = None

    app.dependency_overrides[get_current_user_allowing_temporary_password] = lambda: MockDoctor()

    response = client.get("/api/v1/users/me")

    assert response.status_code == 200
    assert response.json()["full_name"] == "noname@clinic.org"


def test_get_me_works_for_all_roles(client: TestClient):
    """GET /me is accessible to any authenticated role — no privilege check on this endpoint."""

    for role in [UserRole.org_admin, UserRole.nurse, UserRole.superadmin]:

        class MockUser:
            id = f"user-{role}-001"
            email = f"{role}@clinic.org"
            full_name = f"User {role}"
            organization_id = "org-123"
            is_active = True
            must_change_password = False
            document_type = None
            document_number = None

        MockUser.role = role
        app.dependency_overrides[get_current_user_allowing_temporary_password] = lambda u=MockUser(): u

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
    assert resp.json()["detail"] == "This email cannot be used for a new account."


def test_an_org_admin_is_not_told_another_organization_has_the_email(client, db_session, app_log):
    """be-oct26-oraculo-de-correos-entre-organizaciones: the same answer for both, and a probe is logged."""
    org, other = _seed_org(db_session), _seed_org(db_session, "Other NGO")
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _mk_user(db_session, org.id, "ours@clinic.org", UserRole.doctor)
    _mk_user(db_session, other.id, "theirs@other.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: admin

    ours = client.post("/api/v1/users/", json=_new_user_payload("ours@clinic.org"))
    theirs = client.post("/api/v1/users/", json=_new_user_payload("theirs@other.org"))

    assert ours.status_code == theirs.status_code == 400
    assert ours.json() == theirs.json()
    probes = [r for r in app_log.records if "belongs to another organization" in r.getMessage()]
    assert len(probes) == 1


def test_a_superadmin_still_gets_the_explicit_message(client, db_session):
    hq, org = _seed_org(db_session, "HQ"), _seed_org(db_session, "Clinic B")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    _mk_user(db_session, org.id, "taken@clinic.org", UserRole.doctor)
    app.dependency_overrides[get_current_user] = lambda: sa

    resp = client.post("/api/v1/users/", json={**_new_user_payload("taken@clinic.org"),
                                               "organization_id": org.id})

    assert resp.json()["detail"] == "The user with this email already exists in the system."


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


# ---------------------------------------------------------------------------
# Identity document of the professional (the app fills the visit's practitioner)
# ---------------------------------------------------------------------------
#
# Each request closes the session, which detaches what the test created: the
# caller is loaded again per request and results are read with a new query.


def _act_as(db, user, dependency=get_current_user):
    user_id = user.id
    app.dependency_overrides[dependency] = lambda: db.get(User, user_id)


def _stored(db, user_id):
    db.expire_all()
    return db.get(User, user_id)


def test_create_user_stores_the_document(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _act_as(db_session, admin)

    resp = client.post("/api/v1/users/", json=_new_user_payload(
        document_type="CC", document_number="  1.023.456.789 ",
    ))

    assert resp.status_code == 201, resp.text
    assert resp.json()["document_type"] == "CC"
    assert resp.json()["document_number"] == "1.023.456.789"
    stored = db_session.query(User).filter(User.email == "new@clinic.org").one()
    assert (stored.document_type, stored.document_number) == ("CC", "1.023.456.789")


def test_create_user_without_a_document_still_works(client, db_session):
    """The admin screens that do not send it yet keep creating users."""
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _act_as(db_session, admin)

    resp = client.post("/api/v1/users/", json=_new_user_payload())

    assert resp.status_code == 201, resp.text
    assert resp.json()["document_type"] is None
    assert resp.json()["document_number"] is None


def test_a_blank_document_number_counts_as_none(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _act_as(db_session, admin)

    resp = client.post("/api/v1/users/", json=_new_user_payload(document_number=" - "))

    assert resp.status_code == 201, resp.text
    assert resp.json()["document_number"] is None


@pytest.mark.parametrize("extra", [
    {"document_type": "CC"},                                   # number missing
    {"document_number": "1023456789"},                         # type missing
    {"document_type": "TI", "document_number": "1023456789"},  # a minor's document
    {"document_type": "RC", "document_number": "1023456789"},
    {"document_type": "XX", "document_number": "1023456789"},
    {"document_type": "CC", "document_number": "9" * 33},      # longer than the column
])
def test_create_user_rejects_an_incomplete_or_invalid_document(client, db_session, extra):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _act_as(db_session, admin)

    resp = client.post("/api/v1/users/", json=_new_user_payload(**extra))

    assert resp.status_code == 422, resp.text
    assert db_session.query(User).filter(User.email == "new@clinic.org").first() is None


def test_the_same_document_can_have_accounts_in_two_organizations(client, db_session):
    hq = _seed_org(db_session, "HQ")
    sa = _mk_user(db_session, hq.id, "sa@hq.org", UserRole.superadmin)
    org_a_id = _seed_org(db_session, "Org A").id
    org_b_id = _seed_org(db_session, "Org B").id
    _act_as(db_session, sa)
    document = {"document_type": "CE", "document_number": "987654"}

    first = client.post("/api/v1/users/", json=_new_user_payload(
        "doc@a.org", organization_id=org_a_id, **document))
    second = client.post("/api/v1/users/", json=_new_user_payload(
        "doc@b.org", organization_id=org_b_id, **document))

    assert (first.status_code, second.status_code) == (201, 201)


def test_me_and_the_user_list_return_the_document(client, db_session):
    org = _seed_org(db_session)
    admin_id = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin).id
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    doctor.document_type, doctor.document_number = "PPT", "5544332"
    db_session.commit()
    _act_as(db_session, doctor, get_current_user_allowing_temporary_password)
    me = client.get("/api/v1/users/me").json()

    _act_as(db_session, db_session.get(User, admin_id))
    listed = {u["email"]: u for u in client.get("/api/v1/users/").json()}

    assert (me["document_type"], me["document_number"]) == ("PPT", "5544332")
    assert listed["doc@clinic.org"]["document_type"] == "PPT"
    assert listed["doc@clinic.org"]["document_number"] == "5544332"
    assert listed["admin@clinic.org"]["document_type"] is None


def test_me_without_a_document_returns_nulls(client, db_session):
    org = _seed_org(db_session)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    _act_as(db_session, doctor, get_current_user_allowing_temporary_password)

    me = client.get("/api/v1/users/me").json()

    assert me["document_type"] is None
    assert me["document_number"] is None


def test_an_org_admin_sets_and_clears_a_doctors_document(client, db_session, app_log):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    doctor_id = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor).id
    _act_as(db_session, admin)

    resp = client.patch(f"/api/v1/users/{doctor_id}",
                        json={"document_type": "CC", "document_number": "1023456789"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["document_type"], resp.json()["document_number"]) == ("CC", "1023456789")
    assert resp.json()["is_active"] is True  # left out, so unchanged
    assert not any("1023456789" in r.getMessage() for r in app_log.records)

    resp = client.patch(f"/api/v1/users/{doctor_id}",
                        json={"document_type": None, "document_number": None})
    assert resp.status_code == 200, resp.text
    stored = _stored(db_session, doctor_id)
    assert (stored.document_type, stored.document_number) == (None, None)


def test_deactivating_leaves_the_document_alone(client, db_session):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    doctor.document_type, doctor.document_number = "CC", "1023456789"
    db_session.commit()
    doctor_id = doctor.id
    _act_as(db_session, admin)

    resp = client.patch(f"/api/v1/users/{doctor_id}", json={"is_active": False})

    assert resp.status_code == 200, resp.text
    stored = _stored(db_session, doctor_id)
    assert stored.is_active is False
    assert (stored.document_type, stored.document_number) == ("CC", "1023456789")


@pytest.mark.parametrize("body", [
    {},
    {"is_active": None},
    {"document_type": "CC"},
    {"document_number": "1023456789"},
    {"document_type": "CC", "document_number": None},
    {"document_type": "TI", "document_number": "1023456789"},
])
def test_patch_rejects_an_empty_or_incomplete_body(client, db_session, body):
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    _act_as(db_session, admin)

    resp = client.patch(f"/api/v1/users/{doctor.id}", json=body)

    assert resp.status_code == 422, resp.text


def test_nobody_sets_their_own_document(client, db_session):
    """The document goes on every visit the user records: an administrator sets it."""
    org = _seed_org(db_session)
    admin = _mk_user(db_session, org.id, "admin@clinic.org", UserRole.org_admin)
    _act_as(db_session, admin)

    resp = client.patch(f"/api/v1/users/{admin.id}",
                        json={"document_type": "CC", "document_number": "1023456789"})

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "You cannot update your own account."


@pytest.mark.parametrize("role", [UserRole.doctor, UserRole.nurse])
def test_clinical_users_cannot_set_documents(client, db_session, role):
    org = _seed_org(db_session)
    caller = _mk_user(db_session, org.id, "caller@clinic.org", role)
    doctor = _mk_user(db_session, org.id, "doc@clinic.org", UserRole.doctor)
    _act_as(db_session, caller)

    resp = client.patch(f"/api/v1/users/{doctor.id}",
                        json={"document_type": "CC", "document_number": "1023456789"})

    assert resp.status_code == 403, resp.text


def test_an_org_admin_cannot_set_another_organizations_document(client, db_session):
    org_a, org_b = _seed_org(db_session, "Org A"), _seed_org(db_session, "Org B")
    admin_a = _mk_user(db_session, org_a.id, "admin@a.org", UserRole.org_admin)
    doctor_b_id = _mk_user(db_session, org_b.id, "doc@b.org", UserRole.doctor).id
    _act_as(db_session, admin_a)

    resp = client.patch(f"/api/v1/users/{doctor_b_id}",
                        json={"document_type": "CC", "document_number": "1023456789"})

    assert resp.status_code == 403, resp.text
    assert _stored(db_session, doctor_b_id).document_number is None
