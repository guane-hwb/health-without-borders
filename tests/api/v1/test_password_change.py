"""
Changing and resetting passwords.

Audit finding (2026-10-02): be-oct26-contrasenas-sin-cambio-posible. There was no
way to change a password, so the temporary one an administrator set was
permanent, and the stolen-device runbook could not be followed.
"""
import pytest

from app.core.security import TEMPORARY_PASSWORD_LENGTH, get_password_hash
from app.db.models import Organization, User, UserRole

TEMPORARY = "Temporal2026"
CHOSEN = "una frase larga y propia"


def _org(db, name):
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    return org


def _user(db, org, email, role=UserRole.doctor, password=TEMPORARY):
    user = User(email=email, full_name="Persona Sintetica", hashed_password=get_password_hash(password),
                role=role, is_active=True, organization_id=org.id)
    db.add(user)
    db.commit()
    return user


def _login(client, email, password=TEMPORARY):
    return client.post("/api/v1/login/access-token", data={"username": email, "password": password})


def _auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _me(client, tokens):
    return client.get("/api/v1/users/me", headers=_auth(tokens))


def _change(client, tokens, current=TEMPORARY, new=CHOSEN):
    return client.post("/api/v1/users/me/password", headers=_auth(tokens),
                       json={"current_password": current, "new_password": new})


@pytest.fixture
def people(client, db_session):
    clinic, other = _org(db_session, "Clinic"), _org(db_session, "Other")
    users = {
        "admin": _user(db_session, clinic, "admin@clinic.org", UserRole.org_admin),
        "doctor": _user(db_session, clinic, "doc@clinic.org"),
        "nurse": _user(db_session, clinic, "nurse@clinic.org", UserRole.nurse),
        "other_doctor": _user(db_session, other, "doc@other.org"),
        "sa": _user(db_session, other, "sa@hq.org", UserRole.superadmin),
    }
    return {key: user.id for key, user in users.items()}


def _reload(db_session, user_id):
    db_session.expire_all()
    return db_session.query(User).filter(User.id == user_id).one()


def test_user_can_change_own_password_and_old_sessions_die(client, db_session, people):
    this_device = _login(client, "doc@clinic.org").json()
    other_device = _login(client, "doc@clinic.org").json()

    response = _change(client, this_device)

    assert response.status_code == 200
    renewed = response.json()
    assert renewed["must_change_password"] is False and renewed["refresh_token"]
    assert _me(client, renewed).status_code == 200  # this device stays signed in
    assert _me(client, other_device).status_code == 401
    assert _me(client, this_device).status_code == 401
    assert _login(client, "doc@clinic.org").status_code == 401
    assert _login(client, "doc@clinic.org", CHOSEN).status_code == 200
    assert _reload(db_session, people["doctor"]).password_changed_at is not None


def test_a_wrong_current_password_changes_nothing(client, db_session, people):
    tokens = _login(client, "doc@clinic.org").json()

    response = _change(client, tokens, current="no es la actual")

    assert response.status_code == 400  # not 401: the app would refresh and retry
    assert response.json()["detail"] == "The current password is not correct."
    assert _me(client, tokens).status_code == 200
    assert _login(client, "doc@clinic.org").status_code == 200


def test_the_new_password_must_differ(client, db_session, people):
    _user(db_session, db_session.query(Organization).first(), "long@clinic.org", password=CHOSEN)
    tokens = _login(client, "long@clinic.org", CHOSEN).json()

    response = _change(client, tokens, current=CHOSEN, new=CHOSEN)

    assert response.status_code == 400
    assert "different" in response.json()["detail"]


@pytest.mark.parametrize("new", ["corta", "Password1234", "contraseña123", "ñ" * 37])
def test_the_new_password_follows_the_policy(client, db_session, people, new):
    tokens = _login(client, "doc@clinic.org").json()

    response = _change(client, tokens, new=new)

    assert response.status_code == 422
    assert new not in response.text  # the 422 never echoes the password


def test_password_changes_are_rate_limited(client, db_session, people):
    """Guessing the current password counts against the account like a sign-in."""
    tokens = _login(client, "doc@clinic.org").json()

    responses = [_change(client, tokens, current="no es la actual") for _ in range(6)]

    assert [r.status_code for r in responses[:5]] == [400] * 5
    assert responses[5].status_code == 429
    assert responses[5].json()["code"] == "login_paused"
    assert _login(client, "doc@clinic.org").status_code == 429  # the sign-in is paused too


def test_an_admin_reset_gives_a_temporary_password_and_ends_every_session(
    client, db_session, people, app_log
):
    doctor = _login(client, "doc@clinic.org").json()
    admin = _login(client, "admin@clinic.org").json()

    response = client.post(f"/api/v1/users/{people['doctor']}/reset-password", headers=_auth(admin))

    assert response.status_code == 200
    temporary = response.json()["temporary_password"]
    assert len(temporary) == TEMPORARY_PASSWORD_LENGTH
    assert response.headers["cache-control"] == "no-store"
    assert _me(client, doctor).status_code == 401
    after_reset = _login(client, "doc@clinic.org", temporary)
    assert after_reset.json()["must_change_password"] is True
    assert _change(client, after_reset.json(), current=temporary).status_code == 200
    assert _login(client, "doc@clinic.org", CHOSEN).json()["must_change_password"] is False
    logged = " ".join(record.getMessage() for record in app_log.records)
    assert temporary not in logged and CHOSEN not in logged


@pytest.mark.parametrize(("actor", "target", "code"), [
    ("admin", "other_doctor", 403),  # another organization
    ("admin", "sa", 403),
    ("admin", "admin", 400),  # own account: use /users/me/password
    ("nurse", "doctor", 403),
    ("doctor", "nurse", 403),
])
def test_resets_follow_the_management_guards(client, db_session, people, actor, target, code):
    emails = {"admin": "admin@clinic.org", "nurse": "nurse@clinic.org", "doctor": "doc@clinic.org"}
    tokens = _login(client, emails[actor]).json()

    response = client.post(f"/api/v1/users/{people[target]}/reset-password", headers=_auth(tokens))

    assert response.status_code == code
    assert _login(client, "doc@clinic.org").status_code == 200  # nothing was reset


def test_a_reset_lifts_the_pause_of_a_user_who_forgot_their_password(client, db_session, people):
    for _ in range(5):
        assert _login(client, "doc@clinic.org", "no la recuerdo").status_code == 401
    assert _login(client, "doc@clinic.org").status_code == 429
    admin = _login(client, "admin@clinic.org").json()

    reset = client.post(f"/api/v1/users/{people['doctor']}/reset-password", headers=_auth(admin))

    temporary = reset.json()["temporary_password"]
    assert _login(client, "doc@clinic.org", temporary).status_code == 200


def test_a_superadmin_can_reset_an_org_admin(client, db_session, people):
    sa = _login(client, "sa@hq.org").json()

    response = client.post(f"/api/v1/users/{people['admin']}/reset-password", headers=_auth(sa))

    assert response.status_code == 200
    assert _login(client, "admin@clinic.org").status_code == 401


def test_accounts_created_by_an_administrator_must_change_their_password(client, db_session, people):
    admin = _login(client, "admin@clinic.org").json()
    sa = _login(client, "sa@hq.org").json()
    created = client.post("/api/v1/users/", headers=_auth(admin), json={
        "email": "new@clinic.org", "full_name": "Persona Sintetica", "password": TEMPORARY,
        "role": "nurse",
    })
    org = client.post("/api/v1/organizations/", headers=_auth(sa), json={
        "name": "Nueva ONG", "is_active": True,
        "admin": {"email": "admin@nueva.org", "full_name": "Persona Sintetica", "password": TEMPORARY},
    })
    assert created.status_code == 201 and org.status_code == 201

    for email in ("new@clinic.org", "admin@nueva.org"):
        tokens = _login(client, email).json()
        assert tokens["must_change_password"] is True
        assert _me(client, tokens).json()["must_change_password"] is True


def test_existing_accounts_are_not_flagged(client, db_session, people):
    tokens = _login(client, "doc@clinic.org").json()

    assert tokens["must_change_password"] is False
    refreshed = client.post("/api/v1/login/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.json()["must_change_password"] is False
