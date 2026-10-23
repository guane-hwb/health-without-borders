"""
Tests for NFC key version telemetry.

Covers storing sightings, the "latest sighting wins" attribution used by the
usage summary, the role split between bracelets and guardian cards, the
retirement interlock window, and role gating on both endpoints. Also security
review N6 (no UID stored, retention) and N11 (unknown versions, a veto that
made-up sightings cannot clear).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models import NfcKeyVersionObservation, Organization, User, UserRole
from app.services.nfc_key_version_service import device_ref

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


@pytest.fixture(autouse=True)
def ring_with_versions_0_and_1(monkeypatch):
    """The ring from the environment, with versions 0 and 1 (no KEK, whatever .env says)."""
    monkeypatch.setattr(settings, "NFC_KEK", "")
    monkeypatch.setattr(settings, "NFC_MASTER_KEY", "11" * 32)
    monkeypatch.setenv("NFC_KEY_V1", "22" * 32)


@pytest.fixture
def clinician(client: TestClient, db_session):
    org = _create_org(db_session)
    _create_user(db_session, org.id)
    return _token(client)


@pytest.fixture
def superadmin(client: TestClient, db_session):
    org = db_session.query(Organization).first() or _create_org(db_session)
    _create_user(
        db_session,
        org.id,
        role=UserRole.superadmin,
        email="super-keyver@hwb.org",
        user_id="user-keyver-super",
    )
    return _token(client, email="super-keyver@hwb.org")


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

    def test_superadmin_cannot_report(self, client, superadmin):
        # superadmin receives no key material at all, so it can never have
        # legitimately decrypted a chip: a sighting from it is meaningless.
        resp = client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-z", 0)]},
            headers=_auth(superadmin),
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


class TestTimestampContract:
    """
    ``observed_at`` must carry an offset. A naive value would be read as UTC and
    silently shift every sighting by the reporting device's timezone — five
    hours in Colombia — which corrupts the ordering between devices in
    different zones.
    """

    def test_naive_timestamp_is_rejected(self, client, clinician):
        entry = _entry("uid-naive", 0)
        entry["observed_at"] = "2026-09-10T10:00:00"  # no offset

        resp = client.post(
            SYNC_URL, json={"entries": [entry]}, headers=_auth(clinician)
        )

        assert resp.status_code == 422

    def test_utc_timestamp_is_accepted(self, client, clinician):
        entry = _entry("uid-utc", 0)
        entry["observed_at"] = "2026-09-10T10:00:00Z"

        resp = client.post(
            SYNC_URL, json={"entries": [entry]}, headers=_auth(clinician)
        )

        assert resp.status_code == 202

    def test_offset_timestamp_is_accepted(self, client, clinician):
        entry = _entry("uid-offset", 0)
        entry["observed_at"] = "2026-09-10T05:00:00-05:00"

        resp = client.post(
            SYNC_URL, json={"entries": [entry]}, headers=_auth(clinician)
        )

        assert resp.status_code == 202


class TestOrgAdminReporting:
    def test_org_admin_can_report(self, client, admin):
        # org_admin no longer gets keys (N9), but a device that signed in before
        # keeps its ring until the session ends; its batch must not bounce.
        resp = client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-admin", 0)]},
            headers=_auth(admin),
        )

        assert resp.status_code == 202
        assert resp.json()["stored"] == 1


class TestAttributionUsesServerClock:
    def test_a_skewed_client_clock_does_not_flip_attribution(
        self, client, clinician, admin
    ):
        """
        Two sightings of one chip arrive in order: version 0 first, then
        version 1 — but the second carries an *older* client timestamp, as a
        device with a skewed clock would send. The chip has migrated, and the
        summary must say so.
        """
        client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-skew", 0, days_ago=1)]},
            headers=_auth(clinician),
        )
        client.post(
            SYNC_URL,
            json={"entries": [_entry("uid-skew", 1, days_ago=30, header=True)]},
            headers=_auth(clinician),
        )

        body = client.get(USAGE_URL, headers=_auth(admin)).json()

        counts = {(c["key_version"], c["device_role"]): c for c in body["counts"]}
        assert (0, "patient") not in counts
        assert counts[(1, "patient")]["devices"] == 1


class TestNoUidStored:
    """N6: a UID resolves to its patient through /scan; the table keeps an HMAC."""

    def test_rows_keep_a_reference_never_the_uid(self, client, clinician, db_session):
        client.post(SYNC_URL, json={"entries": [_entry("04:a1:b2:c3:d4:e5:f6", 0)]},
                    headers=_auth(clinician))

        row = db_session.query(NfcKeyVersionObservation).one()
        assert row.device_ref == device_ref("04A1B2C3D4E5F6")
        assert len(row.device_ref) == 64
        assert "04A1B2" not in row.device_ref.upper()
        assert not hasattr(row, "device_uid")

    def test_one_chip_written_two_ways_counts_once(self, client, clinician, admin):
        client.post(SYNC_URL, json={"entries": [
            _entry("04:A1:B2:C3", 0), _entry("04a1b2c3", 0),
        ]}, headers=_auth(clinician))

        body = client.get(USAGE_URL, headers=_auth(admin)).json()

        assert body["counts"][0]["devices"] == 1

    def test_the_reference_depends_on_the_secret_key(self, monkeypatch):
        before = device_ref("04A1B2C3")
        monkeypatch.setattr(settings, "SECRET_KEY", "another-secret-key-0123456789abcdef")
        assert device_ref("04A1B2C3") != before


class TestRetention:
    def test_sightings_past_the_retention_are_deleted(self, client, clinician, db_session):
        client.post(SYNC_URL, json={"entries": [_entry("uid-old", 0)]}, headers=_auth(clinician))
        old = db_session.query(NfcKeyVersionObservation).one()
        old.reported_at = datetime.now(timezone.utc) - timedelta(
            days=settings.NFC_KEY_SIGHTING_RETENTION_DAYS + 1
        )
        db_session.commit()

        client.post(SYNC_URL, json={"entries": [_entry("uid-new", 0)]}, headers=_auth(clinician))

        db_session.expire_all()
        assert [r.device_ref for r in db_session.query(NfcKeyVersionObservation)] == [
            device_ref("uid-new")
        ]

    def test_the_window_cannot_be_longer_than_the_retention(self, client, admin):
        longest = settings.NFC_KEY_SIGHTING_RETENTION_DAYS

        assert client.get(f"{USAGE_URL}?window_days={longest}",
                          headers=_auth(admin)).status_code == 200
        assert client.get(f"{USAGE_URL}?window_days={longest + 1}",
                          headers=_auth(admin)).status_code == 422


class TestPoisoning:
    """N11: anyone with an account can post any UID and any version."""

    def test_versions_that_do_not_exist_are_dropped(self, client, clinician, admin):
        resp = client.post(SYNC_URL, json={"entries": [
            _entry("uid-1", 0), _entry("uid-2", 7, header=True),
            _entry("uid-3", 255, header=True),
        ]}, headers=_auth(clinician))

        assert resp.status_code == 202
        assert resp.json() == {"received": 3, "stored": 1}
        body = client.get(USAGE_URL, headers=_auth(admin)).json()
        assert {c["key_version"] for c in body["counts"]} == {0}

    def test_made_up_sightings_on_the_current_version_keep_the_old_vetoed(
        self, client, clinician, admin
    ):
        client.post(SYNC_URL, json={"entries": [
            _entry("uid-rare", 0, days_ago=200),  # a patient who rarely comes back
            *(_entry(f"uid-{i}", 0, days_ago=20) for i in range(3)),
        ]}, headers=_auth(clinician))
        # Then someone reports the recently seen chips as already on version 1.
        client.post(SYNC_URL, json={"entries": [
            _entry(f"uid-{i}", 1, header=True) for i in range(3)
        ]}, headers=_auth(clinician))

        body = client.get(f"{USAGE_URL}?window_days=90", headers=_auth(admin)).json()

        # Attribution follows the latest sighting: only the rare chip is left on 0...
        assert {(c["key_version"], c["devices"]) for c in body["counts"]} == {(0, 1), (1, 3)}
        # ...but the real version 0 sightings inside the window still veto it.
        # Counted by latest sighting, version 0 would have been listed here.
        assert body["retirable_versions"] == []
