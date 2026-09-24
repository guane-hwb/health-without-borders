"""
Deactivating an organization (or a user) must cut access immediately.

Audit findings: be-v2-organizacion-desactivada-conserva-acceso,
x-v2-usuario-inactivo-400-bloquea-pendientes.

These tests use real users and real tokens — no dependency_overrides of
get_current_user — so the whole authentication path is exercised.
"""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_password_hash
from app.db.models import Organization, User, UserRole
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD

PASSWORD = "ValidPass123"


def _org(db, name):
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _user(db, org, email, role):
    user = User(
        email=email,
        full_name="Persona Sintetica",
        hashed_password=get_password_hash(PASSWORD),
        role=role,
        is_active=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, email):
    return client.post(
        "/api/v1/login/access-token", data={"username": email, "password": PASSWORD}
    )


def _bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def clinic(client: TestClient, db_session):
    hq = _org(db_session, "HQ")
    superadmin = _user(db_session, hq, "sa@hq.org", UserRole.superadmin)
    org = _org(db_session, "Org B")
    doctor = _user(db_session, org, "doc@b.org", UserRole.doctor)
    # Plain values: the client fixture closes the session after each request,
    # which detaches these ORM instances.
    ids = {"org_id": org.id, "doctor_id": doctor.id, "doctor_email": doctor.email}
    sa_tokens = _login(client, superadmin.email).json()
    doc_tokens = _login(client, ids["doctor_email"]).json()
    return {**ids, "sa": _bearer(sa_tokens), "doc": doc_tokens}


def _set_org_active(client, clinic, active):
    response = client.patch(
        f"/api/v1/organizations/{clinic['org_id']}",
        headers=clinic["sa"],
        json={"is_active": active},
    )
    assert response.status_code == 200, response.text


ORG_INACTIVE = {"detail": "Organization is inactive.", "code": "organization_inactive"}


def test_deactivated_org_users_are_blocked_everywhere(client, db_session, clinic):
    """poc03: users of a deactivated organization kept logging in, receiving
    the NFC keyring and reading minors' records."""
    headers = _bearer(clinic["doc"])
    _set_org_active(client, clinic, False)

    login = _login(client, clinic["doctor_email"])
    assert login.status_code == 401
    assert login.json() == ORG_INACTIVE
    assert "nfc_keyring" not in login.text

    refresh = client.post(
        "/api/v1/login/refresh", json={"refresh_token": clinic["doc"]["refresh_token"]}
    )
    assert refresh.status_code == 401
    assert refresh.json() == ORG_INACTIVE

    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 403
    assert me.json() == ORG_INACTIVE

    search = client.post(
        "/api/v1/patients/search",
        headers=headers,
        json={
            "document_number": "SINT-0001",
            "birth_date": "2019-05-05",
            "first_name": "Nino",
            "last_name": "Prueba",
        },
    )
    assert search.status_code == 403

    sync = client.post(
        "/api/v1/patients/sync", headers=headers, json=deepcopy(MOCK_PATIENT_PAYLOAD)
    )
    assert sync.status_code == 403
    assert sync.json()["code"] == "organization_inactive"


def test_reactivated_org_users_sign_in_again(client, db_session, clinic):
    """Deactivation revokes every session: reactivating restores the accounts,
    not the tokens issued before (a stolen device's among them)."""
    _set_org_active(client, clinic, False)
    _set_org_active(client, clinic, True)

    assert client.get("/api/v1/users/me", headers=_bearer(clinic["doc"])).status_code == 401
    refresh = client.post(
        "/api/v1/login/refresh", json={"refresh_token": clinic["doc"]["refresh_token"]}
    )
    assert refresh.status_code == 401
    login = _login(client, clinic["doctor_email"])
    assert login.status_code == 200
    assert client.get("/api/v1/users/me", headers=_bearer(login.json())).status_code == 200


def test_refresh_of_deactivated_user_carries_the_code(client, db_session, clinic):
    doctor = db_session.query(User).filter(User.id == clinic["doctor_id"]).one()
    doctor.is_active = False
    db_session.commit()

    refresh = client.post(
        "/api/v1/login/refresh", json={"refresh_token": clinic["doc"]["refresh_token"]}
    )

    assert refresh.status_code == 401
    assert refresh.json() == {"detail": "Inactive user", "code": "user_inactive"}


def test_refresh_of_unknown_user_is_a_plain_401(client, db_session):
    from app.core.security import create_refresh_token

    refresh = client.post(
        "/api/v1/login/refresh",
        json={"refresh_token": create_refresh_token("nadie@org.org")},
    )

    assert refresh.status_code == 401
    assert refresh.json() == {"detail": "Invalid or expired refresh token"}
