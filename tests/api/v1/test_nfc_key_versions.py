"""
Tests for NFC key version telemetry.

Covers storing sightings, the "latest sighting wins" attribution used by the
usage summary, the role split between bracelets and guardian cards, the
retirement interlock window, and role gating on both endpoints.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_password_hash
from app.db.models import Organization, User, UserRole

SYNC_URL = "/api/v1/patients/nfc-key-versions"
USAGE_URL = "/api/v1/patients/nfc-key-versions/usage"


def _create_org(db, org_id: str = "org-keyver") -> Organization:
    org = Organization(id=org_id, name="Keyver Clinic", is_active=True)
    db.add(org)
    db.commit()
    return org


def _create_user(
    db,
    org_id: str,
    role: UserRole = UserRole.doctor,
    email: str = "keyver@hwb.org",
    user_id: str = "user-keyver",
) -> User:
    user = User(
        id=user_id,
        organization_id=org_id,
        full_name="Keyver Tester",
        email=email,
        hashed_password=get_password_hash("SecurePass123"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _token(client: TestClient, email: str = "keyver@hwb.org") -> str:
    resp = client.post(
        "/api/v1/login/access-token",
        data={"username": email, "password": "SecurePass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _entry(uid: str, version: int, *, role="patient", days_ago=0, header=False):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "device_uid": uid,
        "device_role": role,
        "key_version": version,
        "had_header": header,
        "observed_at": when.isoformat(),
    }


@pytest.fixture
def clinician(client: TestClient, db_session):
    org = _create_org(db_session)
    _create_user(db_session, org.id)
    return _token(client)


@pytest.fixture
def admin(client: TestClient, db_session):
    org = db_session.query(Organization).first() or _create_org(db_session)
    _create_user(
        db_session,
        org.id,
        role=UserRole.org_admin,
        email="admin-keyver@hwb.org",
        user_id="user-keyver-admin",
    )
    return _token(client, email="admin-keyver@hwb.org")


class TestSyncEndpoint:
    def test_stores_a_batch(self, client, clinician):
        resp = client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-1", 0), _entry("uid-2", 1, header=True)]},
            headers=_auth(clinician),
        )

        assert resp.status_code == 202
        assert resp.json() == {"received": 2, "stored": 2}

    def test_repeat_sightings_are_all_kept(self, client, clinician):
        # Append-only: the interval between sightings is the point.
        for _ in range(3):
            resp = client.post(
                SYNC_URL,
                json={"entries": [_entry("uid-repeat", 0)]},
                headers=_auth(clinician),
            )
            assert resp.status_code == 202
            assert resp.json()["stored"] == 1

    def test_unknown_role_is_normalised_not_rejected(self, client, clinician):
        resp = client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-x", 0, role="future-role")]},
            headers=_auth(clinician),
        )
        # A future device role must never drop a whole batch.
        assert resp.status_code == 202
        assert resp.json()["stored"] == 1

    def test_empty_batch_rejected(self, client, clinician):
        resp = client.post(
            SYNC_URL, json={"entries": []}, headers=_auth(clinician)
        )
        assert resp.status_code == 422

    def test_version_out_of_header_range_rejected(self, client, clinician):
        resp = client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-y", 256)]},
            headers=_auth(clinician),
        )
        assert resp.status_code == 422

    def test_admin_cannot_report(self, client, admin):
        resp = client.post(
            SYNC_URL, json={"entries": [_entry("uid-z", 0)]}, headers=_auth(admin)
        )
        assert resp.status_code == 403

    def test_requires_authentication(self, client):
        resp = client.post(SYNC_URL, json={"entries": [_entry("uid-a", 0)]})
        assert resp.status_code in (401, 403)


class TestUsageSummary:
    def test_counts_distinct_devices(self, client, clinician, admin):
        client.post(
            SYNC_URL,
            json={
                "entries": [
                    _entry("uid-1", 0),
                    _entry("uid-1", 0),  # same chip read twice
                    _entry("uid-2", 0),
                ]
            },
            headers=_auth(clinician),
        )

        body = client.get(USAGE_URL, headers=_auth(admin)).json()

        counts = {(c["key_version"], c["device_role"]): c for c in body["counts"]}
        # Two chips, not three sightings.
        assert counts[(0, "patient")]["devices"] == 2

    def test_latest_sighting_wins(self, client, clinician, admin):
        client.post(
            SYNC_URL,
            json={
                "entries": [
                    _entry("uid-moved", 0, days_ago=30),
                    _entry("uid-moved", 1, days_ago=1, header=True),
                ]
            },
            headers=_auth(clinician),
        )

        body = client.get(USAGE_URL, headers=_auth(admin)).json()

        counts = {(c["key_version"], c["device_role"]): c for c in body["counts"]}
        # The chip migrated; counting it under both would overstate the damage
        # a retirement of version 0 would do.
        assert (0, "patient") not in counts
        assert counts[(1, "patient")]["devices"] == 1

    def test_roles_are_reported_separately(self, client, clinician, admin):
        client.post(
            SYNC_URL,
            json={
                "entries": [
                    _entry("uid-brac", 0, role="patient"),
                    _entry("uid-card", 0, role="guardian"),
                ]
            },
            headers=_auth(clinician),
        )

        body = client.get(USAGE_URL, headers=_auth(admin)).json()

        counts = {(c["key_version"], c["device_role"]): c for c in body["counts"]}
        # Guardian cards are rewritten less often, so counting bracelets alone
        # would hide the likelier stragglers.
        assert counts[(0, "patient")]["devices"] == 1
        assert counts[(0, "guardian")]["devices"] == 1

    def test_version_seen_recently_is_not_retirable(
        self, client, clinician, admin
    ):
        client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-live", 0, days_ago=5)]},
            headers=_auth(clinician),
        )

        body = client.get(USAGE_URL, headers=_auth(admin)).json()

        # The interlock: a version still being read cannot be retired.
        assert body["retirable_versions"] == []

    def test_version_not_seen_in_window_is_listed(
        self, client, clinician, admin
    ):
        client.post(
            SYNC_URL,
            json={
                "entries": [
                    _entry("uid-old", 0, days_ago=200),
                    _entry("uid-new", 1, days_ago=2, header=True),
                ]
            },
            headers=_auth(clinician),
        )

        body = client.get(f"{USAGE_URL}?window_days=90", headers=_auth(admin)).json()

        assert body["window_days"] == 90
        assert body["retirable_versions"] == [0]

    def test_window_widens_to_cover_older_sightings(
        self, client, clinician, admin
    ):
        client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-old", 0, days_ago=200)]},
            headers=_auth(clinician),
        )

        body = client.get(f"{USAGE_URL}?window_days=365", headers=_auth(admin)).json()

        assert body["retirable_versions"] == []

    def test_empty_summary(self, client, admin):
        body = client.get(USAGE_URL, headers=_auth(admin)).json()
        assert body["counts"] == []
        assert body["retirable_versions"] == []

    def test_invalid_window_rejected(self, client, admin):
        assert client.get(f"{USAGE_URL}?window_days=0", headers=_auth(admin)).status_code == 422
        assert client.get(f"{USAGE_URL}?window_days=99999", headers=_auth(admin)).status_code == 422

    def test_clinician_cannot_read_summary(self, client, clinician):
        resp = client.get(USAGE_URL, headers=_auth(clinician))
        assert resp.status_code == 403
