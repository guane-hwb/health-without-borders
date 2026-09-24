"""
Per-user session revocation (users.token_version) and id-bound tokens.

Audit findings: be-v2-sesiones-no-revocables-token-ligado-a-email (poc18),
be-v2-revocacion-no-corta-sesion-del-dispositivo, be-v2-email-sensible-a-mayusculas,
be-v2-bcrypt-trunca-72-bytes.
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.db.models import Organization, User, UserRole

PASSWORD = "ValidPass123"


def _org(db, name):
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _user(db, org, email, role=UserRole.doctor):
    user = User(
        email=email, full_name="Persona Sintetica", hashed_password=get_password_hash(PASSWORD),
        role=role, is_active=True, organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, email, password=PASSWORD):
    return client.post("/api/v1/login/access-token", data={"username": email, "password": password})


def _me(client, access):
    return client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access}"})


def _refresh(client, refresh):
    return client.post("/api/v1/login/refresh", json={"refresh_token": refresh})


@pytest.fixture
def accounts(client, db_session):
    org = _org(db_session, "Clinic")
    hq = _org(db_session, "HQ")
    admin = _user(db_session, org, "admin@clinic.org", UserRole.org_admin)
    doctor = _user(db_session, org, "doc@clinic.org")
    sa = _user(db_session, hq, "sa@hq.org", UserRole.superadmin)
    return {
        "org_id": org.id, "doctor_id": doctor.id,
        "admin": _login(client, admin.email).json(),
        "doctor": _login(client, doctor.email).json(),
        "sa": _login(client, sa.email).json(),
    }


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_new_tokens_carry_the_user_id_and_version(client, db_session, accounts):
    claims = jwt.decode(
        accounts["doctor"]["access_token"], settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    assert claims["sub"] == accounts["doctor_id"]
    assert claims["tv"] == 0


def test_reactivation_does_not_revive_old_refresh(client, db_session, accounts):
    """poc18: a refresh token issued before the deactivation worked again after it."""
    url = f"/api/v1/users/{accounts['doctor_id']}"
    assert client.patch(url, headers=_auth(accounts["admin"]), json={"is_active": False}).status_code == 200
    assert client.patch(url, headers=_auth(accounts["admin"]), json={"is_active": True}).status_code == 200

    assert _refresh(client, accounts["doctor"]["refresh_token"]).status_code == 401
    assert _me(client, accounts["doctor"]["access_token"]).status_code == 401
    assert _login(client, "doc@clinic.org").status_code == 200


def test_revoke_sessions_signs_the_user_out_everywhere(client, db_session, accounts):
    second_device = _login(client, "doc@clinic.org").json()

    response = client.post(
        f"/api/v1/users/{accounts['doctor_id']}/revoke-sessions", headers=_auth(accounts["admin"])
    )

    assert response.status_code == 204
    for tokens in (accounts["doctor"], second_device):
        assert _me(client, tokens["access_token"]).status_code == 401
        assert _refresh(client, tokens["refresh_token"]).status_code == 401
    fresh = _login(client, "doc@clinic.org").json()
    assert _me(client, fresh["access_token"]).status_code == 200


def test_revoke_sessions_uses_the_management_guards(client, db_session, accounts):
    other = _org(db_session, "Other")
    stranger = _user(db_session, other, "doc@other.org")

    doctor_attempt = client.post(
        f"/api/v1/users/{stranger.id}/revoke-sessions", headers=_auth(accounts["doctor"])
    )
    cross_org = client.post(
        f"/api/v1/users/{stranger.id}/revoke-sessions", headers=_auth(accounts["admin"])
    )

    assert doctor_attempt.status_code == 403
    assert cross_org.status_code == 403


def test_refresh_reuse_revokes_the_whole_family(client, db_session, accounts):
    first = accounts["doctor"]["refresh_token"]
    rotated = _refresh(client, first)
    assert rotated.status_code == 200

    assert _refresh(client, first).status_code == 401  # reuse of a rotated token

    # The copy and the legitimate chain are both dead now.
    assert _refresh(client, rotated.json()["refresh_token"]).status_code == 401
    assert _me(client, rotated.json()["access_token"]).status_code == 401


def test_reuse_of_a_token_for_a_deleted_account_is_just_rejected(client, db_session, accounts):
    first = accounts["doctor"]["refresh_token"]
    assert _refresh(client, first).status_code == 200
    doctor = db_session.query(User).filter(User.id == accounts["doctor_id"]).one()
    db_session.delete(doctor)
    db_session.commit()

    assert _refresh(client, first).status_code == 401


def test_recreated_email_does_not_inherit_tokens(client, db_session, accounts):
    """poc18: tokens of a deleted account kept working for a new account with
    the same email, because tokens named the user by email."""
    legacy_token = create_access_token("doc@clinic.org")  # pre-September 2026 shape
    new_style = accounts["doctor"]["access_token"]
    doctor = db_session.query(User).filter(User.id == accounts["doctor_id"]).one()
    org = db_session.query(Organization).filter(Organization.id == accounts["org_id"]).one()
    db_session.delete(doctor)
    db_session.commit()
    recreated = _user(db_session, org, "doc@clinic.org")
    recreated.created_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    db_session.commit()

    assert _me(client, legacy_token).status_code == 401
    assert _me(client, new_style).status_code == 401


def test_legacy_email_token_still_works_for_existing_accounts(client, db_session, accounts):
    """Tokens issued before the deploy (sub = email, no tv) keep working, so
    the deploy signs nobody out."""
    doctor = db_session.query(User).filter(User.id == accounts["doctor_id"]).one()
    doctor.created_at = None  # an account that predates the column
    db_session.commit()

    assert _me(client, create_access_token("DOC@clinic.org")).status_code == 200


def test_deactivating_an_organization_revokes_its_sessions(client, db_session, accounts):
    url = f"/api/v1/organizations/{accounts['org_id']}"
    assert client.patch(url, headers=_auth(accounts["sa"]), json={"is_active": False}).status_code == 200
    assert client.patch(url, headers=_auth(accounts["sa"]), json={"is_active": True}).status_code == 200

    assert _refresh(client, accounts["doctor"]["refresh_token"]).status_code == 401


# ---------------------------------------------------------------------------
# Email case and bcrypt limit
# ---------------------------------------------------------------------------


def test_login_ignores_email_case(client, db_session, accounts):
    assert _login(client, "  DOC@Clinic.ORG ").status_code == 200


def test_created_emails_are_lower_case_and_unique_regardless_of_case(client, db_session, accounts):
    body = {"email": "Maria.Lopez@Clinic.org", "full_name": "Maria Lopez",
            "password": "SecurePassword123!", "role": "doctor"}

    created = client.post("/api/v1/users/", headers=_auth(accounts["admin"]), json=body)
    duplicate = client.post(
        "/api/v1/users/", headers=_auth(accounts["admin"]),
        json={**body, "email": "maria.lopez@clinic.org"},
    )

    assert created.status_code == 201
    assert created.json()["email"] == "maria.lopez@clinic.org"
    assert duplicate.status_code == 400


def test_org_admin_email_is_normalised_too(client, db_session, accounts):
    response = client.post(
        "/api/v1/organizations/", headers=_auth(accounts["sa"]),
        json={"name": "Nueva Org", "admin": {
            "full_name": "Admin Nuevo", "email": "Admin@Nueva.org", "password": "SecurePassword1",
        }},
    )

    assert response.status_code == 201
    assert _login(client, "admin@nueva.org", "SecurePassword1").status_code == 200


def test_passwords_longer_than_72_bytes_are_rejected(client, db_session, accounts):
    response = client.post(
        "/api/v1/users/", headers=_auth(accounts["admin"]),
        json={"email": "largo@clinic.org", "full_name": "Clave Larga",
              "password": "ñ" * 37, "role": "doctor"},  # 74 bytes in UTF-8
    )

    assert response.status_code == 422
    assert "at most 72 bytes" in response.text


def test_a_token_without_subject_belongs_to_nobody(db_session):
    from app.api.deps import user_for_token

    assert user_for_token(db_session, {"type": "access"}) is None
