"""
Per-account limit on failed sign-ins, shared through the database.

Audit finding (2026-10-02): be-oct26-rate-limit-por-instancia-y-solo-por-ip. The
limit was per IP and per instance only, so one account could be guessed from
many addresses or across many instances without any limit.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models import LoginFailure, Organization, User, UserRole
from app.services import login_throttle

PASSWORD = "Temporal2026"


@pytest.fixture
def accounts(client, db_session):
    org = Organization(name="Clinic", is_active=True)
    db_session.add(org)
    db_session.commit()
    for email in ("doc@clinic.org", "nurse@clinic.org"):
        db_session.add(User(email=email, full_name="Persona Sintetica", role=UserRole.doctor,
                            hashed_password=get_password_hash(PASSWORD), is_active=True,
                            organization_id=org.id))
    db_session.commit()


def _login(client, email="doc@clinic.org", password="no es esta", ip="198.51.100.1"):
    return client.post("/api/v1/login/access-token", data={"username": email, "password": password},
                       headers={"X-Forwarded-For": ip})


def _row(db_session, email="doc@clinic.org"):
    db_session.expire_all()
    return db_session.get(LoginFailure, login_throttle.account_key(email))


def _move_back(db_session, email="doc@clinic.org", **fields):
    row = _row(db_session, email)
    for name, seconds in fields.items():
        setattr(row, name, datetime.now(timezone.utc) - timedelta(seconds=seconds))
    db_session.commit()


def test_failed_logins_are_limited_per_account_across_ips(client, db_session, accounts):
    codes = [_login(client, ip=f"198.51.100.{i}").status_code for i in range(1, 6)]

    paused = _login(client, password=PASSWORD, ip="203.0.113.9")  # the right password, elsewhere

    assert codes == [401] * 5
    assert paused.status_code == 429
    assert paused.json()["code"] == "login_paused"
    assert 0 < int(paused.headers["retry-after"]) <= settings.LOGIN_DELAY_BASE_SECONDS
    assert _login(client, "nurse@clinic.org", PASSWORD).status_code == 200  # other accounts


def test_an_account_that_does_not_exist_is_paused_the_same_way(client, db_session, accounts):
    for _ in range(5):
        assert _login(client, "nobody@clinic.org").status_code == 401

    response = _login(client, "nobody@clinic.org")

    assert response.status_code == 429
    assert response.json()["code"] == "login_paused"


def test_each_failure_after_a_pause_doubles_it_up_to_the_maximum(client, db_session, accounts):
    for _ in range(5):
        _login(client)
    _move_back(db_session, blocked_until=1)  # the first pause is over

    assert _login(client).status_code == 401
    assert int(_login(client).headers["retry-after"]) > settings.LOGIN_DELAY_BASE_SECONDS

    row = _row(db_session)
    row.failures, row.blocked_until = 40, None
    db_session.commit()
    _login(client)
    assert int(_login(client).headers["retry-after"]) <= settings.LOGIN_DELAY_MAX_SECONDS


def test_the_right_password_after_the_pause_clears_the_count(client, db_session, accounts):
    for _ in range(5):
        _login(client)
    _move_back(db_session, blocked_until=1)

    assert _login(client, password=PASSWORD).status_code == 200
    assert _row(db_session) is None


def test_old_failures_do_not_count(client, db_session, accounts):
    for _ in range(4):
        _login(client)
    _move_back(db_session, first_failure_at=settings.LOGIN_FAILURE_WINDOW_SECONDS + 60)

    assert _login(client).status_code == 401
    assert _row(db_session).failures == 1
    assert _login(client, password=PASSWORD).status_code == 200


def test_email_case_does_not_matter(client, db_session, accounts):
    for email in ("DOC@clinic.org", "doc@CLINIC.org", " doc@clinic.org", "Doc@Clinic.Org", "doc@clinic.org"):
        _login(client, email)

    assert _login(client, password=PASSWORD).status_code == 429


def test_the_table_holds_no_email_and_forgets_old_streaks(client, db_session, accounts):
    _login(client, "nurse@clinic.org")
    _move_back(db_session, "nurse@clinic.org",
               last_failure_at=settings.LOGIN_FAILURE_WINDOW_SECONDS + 60)

    _login(client)

    rows = db_session.query(LoginFailure).all()
    assert [len(row.key) for row in rows] == [64]
    assert all("@" not in row.key for row in rows)
    assert _row(db_session, "nurse@clinic.org") is None
