"""
Tests for the database-backed NFC keyring and revocation.

The point of all of this is a single operational capability: making a leaked key
stop working without a redeploy. These tests cover the wrapping, the ring being
served from the database, revocation and its consequences, and the fallback that
keeps deployments without a KEK behaving exactly as before.
"""
import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import settings
from app.core.key_wrapper import EnvKekWrapper, KeyWrapperError, generate_key
from app.core.nfc_startup import prepare_nfc_keyring
from app.core.security import get_password_hash
from app.db.models import (
    NfcKey,
    NfcKeyEvent,
    NfcKeyringState,
    Organization,
    User,
    UserRole,
)
from app.services import nfc_key_service

KEK = "ab" * 32
OTHER_KEK = "cd" * 32
MASTER = "11" * 32

REVOKE_URL = "/api/v1/patients/nfc-keys/revoke"
STATUS_URL = "/api/v1/patients/nfc-keys"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_keyring_state(monkeypatch):
    """Isolate key configuration and drop the cached ring between tests."""
    monkeypatch.setattr(settings, "NFC_MASTER_KEY", MASTER)
    monkeypatch.setattr(settings, "NFC_CURRENT_KEY_VERSION", 0)
    monkeypatch.setattr(settings, "NFC_KEK", "")
    nfc_key_service.invalidate_cache()
    yield
    nfc_key_service.invalidate_cache()


def _create_user(db, role: UserRole, email: str, user_id: str) -> User:
    org = db.query(Organization).first()
    if org is None:
        org = Organization(id="org-keys", name="Keys Clinic", is_active=True)
        db.add(org)
        db.commit()
    user = User(
        id=user_id,
        organization_id=org.id,
        full_name="Keys Tester",
        email=email,
        hashed_password=get_password_hash("SecurePass123"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _token(client: TestClient, email: str) -> str:
    resp = client.post(
        "/api/v1/login/access-token",
        data={"username": email, "password": "SecurePass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def superadmin(client: TestClient, db_session):
    _create_user(db_session, UserRole.superadmin, "super@hwb.org", "u-super")
    return _token(client, "super@hwb.org")


@pytest.fixture
def doctor(client: TestClient, db_session):
    _create_user(db_session, UserRole.doctor, "doc@hwb.org", "u-doc")
    return _token(client, "doc@hwb.org")


@pytest.fixture
def with_kek(monkeypatch, db_session):
    """A configured KEK with version 0 imported, as a live deployment would be."""
    monkeypatch.setattr(settings, "NFC_KEK", KEK)
    nfc_key_service.invalidate_cache()
    nfc_key_service.ensure_initialised(db_session)
    return KEK


# ---------------------------------------------------------------------------
# Wrapping
# ---------------------------------------------------------------------------

class TestKeyWrapper:
    def test_round_trip(self):
        wrapper = EnvKekWrapper(KEK)
        key = generate_key()

        assert wrapper.unwrap(wrapper.wrap(key, 3), 3) == key

    def test_wrapping_is_randomised(self):
        wrapper = EnvKekWrapper(KEK)
        key = generate_key()

        # A fresh nonce per wrap: two rows of the same key must not look alike.
        assert wrapper.wrap(key, 1) != wrapper.wrap(key, 1)

    def test_a_row_cannot_be_moved_to_another_version(self):
        wrapper = EnvKekWrapper(KEK)
        wrapped = wrapper.wrap(generate_key(), 1)

        # The version is authenticated, so re-labelling a row fails instead of
        # yielding a key that would encrypt chips nobody can read.
        with pytest.raises(KeyWrapperError):
            wrapper.unwrap(wrapped, 2)

    def test_the_wrong_kek_cannot_unwrap(self):
        wrapped = EnvKekWrapper(KEK).wrap(generate_key(), 1)

        with pytest.raises(KeyWrapperError):
            EnvKekWrapper(OTHER_KEK).unwrap(wrapped, 1)

    def test_kek_id_is_a_fingerprint_not_the_secret(self):
        wrapper = EnvKekWrapper(KEK)

        assert wrapper.kek_id not in KEK
        assert len(wrapper.kek_id) == 16
        assert EnvKekWrapper(KEK).kek_id == wrapper.kek_id
        assert EnvKekWrapper(OTHER_KEK).kek_id != wrapper.kek_id

    def test_malformed_kek_is_rejected(self):
        with pytest.raises(KeyWrapperError):
            EnvKekWrapper("not-hex")
        with pytest.raises(KeyWrapperError):
            EnvKekWrapper("abcd")

    def test_corrupt_row_is_rejected(self):
        wrapper = EnvKekWrapper(KEK)

        with pytest.raises(KeyWrapperError):
            wrapper.unwrap("not base64!!", 1)
        with pytest.raises(KeyWrapperError):
            wrapper.unwrap("YWJj", 1)  # valid base64, too short


# ---------------------------------------------------------------------------
# The ring from the database
# ---------------------------------------------------------------------------

class TestKeyringSource:
    def test_without_a_kek_the_ring_comes_from_the_environment(self, db_session):
        # A deployment that has not adopted the KEK must not change behaviour.
        assert nfc_key_service.load_keyring(db_session) is None

        claims = security.nfc_key_claims(role=UserRole.doctor, db=db_session)
        assert claims["nfc_keyring"] == {"0": MASTER}
        assert claims["nfc_key_version"] == 0

    def test_version_zero_is_imported_once(self, with_kek, db_session):
        rows = db_session.query(NfcKey).all()
        assert [r.version for r in rows] == [0]
        assert rows[0].wrapped_key != MASTER  # stored wrapped, never in clear

        # Importing again must not duplicate or re-wrap.
        nfc_key_service.ensure_initialised(db_session)
        assert db_session.query(NfcKey).count() == 1

    def test_the_imported_key_is_the_one_devices_receive(
        self, with_kek, db_session
    ):
        claims = security.nfc_key_claims(role=UserRole.doctor, db=db_session)

        assert claims["nfc_keyring"] == {"0": MASTER}
        assert claims["nfc_encryption_key"] == MASTER

    def test_an_unwrappable_row_is_skipped_not_fatal(self, with_kek, db_session):
        db_session.add(
            NfcKey(
                version=7,
                wrapped_key="Y29ycnVwdGVkLXJvdw==",
                kek_id="deadbeefdeadbeef",
                status="live",
            )
        )
        db_session.commit()
        nfc_key_service.invalidate_cache()

        ring = nfc_key_service.load_keyring(db_session)

        # One damaged row must not take NFC away from every device.
        assert 7 not in ring["keys"]
        assert ring["keys"][0] == MASTER

    def test_the_import_is_recorded(self, with_kek, db_session):
        events = db_session.query(NfcKeyEvent).all()

        assert [(e.action, e.version) for e in events] == [("imported", 0)]


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

class TestRevocation:
    def _revoke(self, client, token, version=0, ack=True, reason="key leaked"):
        return client.post(
            REVOKE_URL,
            json={
                "version": version,
                "reason": reason,
                "acknowledge_chip_impact": ack,
            },
            headers=_auth(token),
        )

    def test_revoking_the_current_version_creates_a_replacement(
        self, client, superadmin, with_kek, db_session
    ):
        resp = self._revoke(client, superadmin)

        assert resp.status_code == 200
        body = resp.json()
        assert body["revoked_version"] == 0
        assert body["was_current"] is True
        # Something has to be current or nothing could be written at all.
        assert body["replacement_version"] == 1

    def test_a_revoked_version_stops_being_served(
        self, client, superadmin, with_kek, db_session
    ):
        self._revoke(client, superadmin)
        nfc_key_service.invalidate_cache()
        db_session.expire_all()

        claims = security.nfc_key_claims(role=UserRole.doctor, db=db_session)

        # Delivery stops for reading too: the leaked key is the one that reads.
        assert "0" not in (claims["nfc_keyring"] or {})
        assert claims["nfc_key_version"] == 1
        assert claims["nfc_encryption_key"] != MASTER

    def test_revocation_is_recorded_with_actor_and_reason(
        self, client, superadmin, with_kek, db_session
    ):
        self._revoke(client, superadmin, reason="device stolen in Cúcuta")
        db_session.expire_all()

        revoked = (
            db_session.query(NfcKeyEvent)
            .filter(NfcKeyEvent.action == "revoked")
            .one()
        )
        assert revoked.version == 0
        assert revoked.actor_id == "u-super"
        assert revoked.reason == "device stolen in Cúcuta"

        actions = {e.action for e in db_session.query(NfcKeyEvent).all()}
        # All three operations are logged, not only the revocation.
        assert {"imported", "revoked", "generated", "rotated"} <= actions

    def test_revoking_a_non_current_version_leaves_the_pointer_alone(
        self, client, superadmin, with_kek, db_session
    ):
        self._revoke(client, superadmin, version=0)  # 0 revoked, 1 is current
        db_session.expire_all()

        resp = self._revoke(client, superadmin, version=1)
        assert resp.status_code == 200

        # Version 1 was current, so it too gets a replacement.
        assert resp.json()["replacement_version"] == 2

    def test_already_revoked_is_rejected(
        self, client, superadmin, with_kek, db_session
    ):
        self._revoke(client, superadmin)

        resp = self._revoke(client, superadmin)

        assert resp.status_code == 400

    def test_unknown_version_is_not_found(self, client, superadmin, with_kek):
        resp = self._revoke(client, superadmin, version=99)

        assert resp.status_code == 404

    def test_without_a_kek_revocation_is_refused(self, client, superadmin):
        # Nothing to change at runtime: the ring comes from the environment.
        resp = self._revoke(client, superadmin)

        assert resp.status_code == 400

    def test_acknowledgement_is_required(self, client, superadmin, with_kek):
        resp = self._revoke(client, superadmin, ack=False)

        assert resp.status_code == 422

    def test_a_reason_is_required(self, client, superadmin, with_kek):
        resp = client.post(
            REVOKE_URL,
            json={"version": 0, "acknowledge_chip_impact": True},
            headers=_auth(superadmin),
        )

        assert resp.status_code == 422

    def test_a_doctor_cannot_revoke(self, client, doctor, with_kek):
        resp = self._revoke(client, doctor)

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Keyring status
# ---------------------------------------------------------------------------

class TestKeyringStatus:
    def test_reports_versions_without_key_material(
        self, client, superadmin, with_kek
    ):
        body = client.get(STATUS_URL, headers=_auth(superadmin)).json()

        assert body["source"] == "database"
        assert body["current_version"] == 0
        assert [v["version"] for v in body["versions"]] == [0]
        # Never the key itself.
        assert MASTER not in str(body)

    def test_reports_the_environment_when_there_is_no_kek(
        self, client, superadmin
    ):
        body = client.get(STATUS_URL, headers=_auth(superadmin)).json()

        assert body["source"] == "environment"
        assert body["current_version"] == 0
        assert body["versions"] == []

    def test_works_before_the_keyring_tables_exist(
        self, client, superadmin, db_session
    ):
        # The tables are created by scripts/create_tables.py, not at startup,
        # so without a KEK this endpoint must not query them at all — otherwise
        # merging the feature breaks it until someone runs the script.
        from app.db.models import NfcKey as _NfcKey

        _NfcKey.__table__.drop(db_session.get_bind(), checkfirst=True)
        try:
            resp = client.get(STATUS_URL, headers=_auth(superadmin))

            assert resp.status_code == 200
            assert resp.json()["source"] == "environment"
        finally:
            _NfcKey.__table__.create(db_session.get_bind(), checkfirst=True)

    def test_shows_a_revoked_version_with_its_reason(
        self, client, superadmin, with_kek
    ):
        client.post(
            REVOKE_URL,
            json={
                "version": 0,
                "reason": "laptop compromised",
                "acknowledge_chip_impact": True,
            },
            headers=_auth(superadmin),
        )

        body = client.get(STATUS_URL, headers=_auth(superadmin)).json()

        states = {v["version"]: v for v in body["versions"]}
        assert states[0]["status"] == "revoked"
        assert states[0]["revoke_reason"] == "laptop compromised"
        assert states[1]["status"] == "live"
        assert body["current_version"] == 1

    def test_a_doctor_cannot_read_the_keyring_state(
        self, client, doctor, with_kek
    ):
        resp = client.get(STATUS_URL, headers=_auth(doctor))

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Role gating still applies to the database-backed ring
# ---------------------------------------------------------------------------

def test_superadmin_still_receives_no_key_material(with_kek, db_session):
    claims = security.nfc_key_claims(role=UserRole.superadmin, db=db_session)

    assert claims == {
        "nfc_encryption_key": None,
        "nfc_key_version": None,
        "nfc_keyring": None,
    }


def test_the_pointer_row_exists_after_import(with_kek, db_session):
    state = db_session.query(NfcKeyringState).filter(NfcKeyringState.id == 1).one()

    assert state.current_version == 0


# ---------------------------------------------------------------------------
# Edge cases in assembling and seeding the ring
# ---------------------------------------------------------------------------

class TestRingEdgeCases:
    def test_the_ring_is_cached_between_reads(self, with_kek, db_session):
        first = nfc_key_service.load_keyring(db_session)

        # A row added behind the cache must not appear until it is invalidated:
        # login, refresh and /users/me would otherwise unwrap every key on every
        # request.
        db_session.add(
            NfcKey(
                version=5,
                wrapped_key=EnvKekWrapper(KEK).wrap(generate_key(), 5),
                kek_id=EnvKekWrapper(KEK).kek_id,
                status="live",
            )
        )
        db_session.commit()

        assert nfc_key_service.load_keyring(db_session) == first

        nfc_key_service.invalidate_cache()
        assert 5 in nfc_key_service.load_keyring(db_session)["keys"]

    def test_a_current_version_that_cannot_be_served_is_dropped(
        self, with_kek, db_session
    ):
        # Point at a version that is not live: readable ring, no writes.
        state = (
            db_session.query(NfcKeyringState)
            .filter(NfcKeyringState.id == 1)
            .one()
        )
        state.current_version = 42
        db_session.commit()
        nfc_key_service.invalidate_cache()

        ring = nfc_key_service.load_keyring(db_session)

        assert ring["current"] is None
        assert ring["keys"][0] == MASTER

    def test_import_without_a_master_key_leaves_the_ring_empty(
        self, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "NFC_KEK", KEK)
        monkeypatch.setattr(settings, "NFC_MASTER_KEY", "")
        nfc_key_service.invalidate_cache()

        nfc_key_service.ensure_initialised(db_session)

        assert db_session.query(NfcKey).count() == 0

    def test_import_with_a_malformed_master_key_is_not_fatal(
        self, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "NFC_KEK", KEK)
        monkeypatch.setattr(settings, "NFC_MASTER_KEY", "not-hex-at-all")
        nfc_key_service.invalidate_cache()

        nfc_key_service.ensure_initialised(db_session)

        assert db_session.query(NfcKey).count() == 0

    def test_revoking_without_a_kek_raises(self, db_session):
        with pytest.raises(ValueError):
            nfc_key_service.revoke_version(
                db_session, version=0, actor_id=None, reason="x"
            )

# ---------------------------------------------------------------------------
# Startup preparation
# ---------------------------------------------------------------------------

class TestStartupPreparation:
    def test_does_nothing_without_a_kek(self, db_session):
        # Every deployment behaves this way until the KEK is deliberately set.
        prepare_nfc_keyring(db_session)

        assert db_session.query(NfcKey).count() == 0

    def test_imports_and_validates_with_a_kek(self, monkeypatch, db_session):
        monkeypatch.setattr(settings, "NFC_KEK", KEK)
        nfc_key_service.invalidate_cache()

        prepare_nfc_keyring(db_session)

        assert db_session.query(NfcKey).count() == 1

    def test_a_wrong_kek_refuses_to_start(self, with_kek, monkeypatch, db_session):
        # Rows were wrapped under KEK; start with a different one.
        monkeypatch.setattr(settings, "NFC_KEK", OTHER_KEK)
        nfc_key_service.invalidate_cache()

        # Failing to boot is far better than devices quietly receiving an empty
        # ring and NFC simply not working.
        with pytest.raises(RuntimeError, match="no key could be unwrapped"):
            prepare_nfc_keyring(db_session)

    def test_live_keys_without_a_current_version_refuse_to_start(
        self, with_kek, db_session
    ):
        state = (
            db_session.query(NfcKeyringState)
            .filter(NfcKeyringState.id == 1)
            .one()
        )
        state.current_version = 99
        db_session.commit()
        nfc_key_service.invalidate_cache()

        with pytest.raises(RuntimeError, match="no usable current version"):
            prepare_nfc_keyring(db_session)


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------

class TestKekValidation:
    def test_a_malformed_kek_is_reported(self, monkeypatch):
        monkeypatch.setattr(settings, "NFC_KEK", "not-a-hex-key")

        errors = settings.nfc_keyring_errors()

        assert any("NFC_KEK" in e for e in errors)
        # The message names the variable, never the value.
        assert all("not-a-hex-key" not in e for e in errors)

    def test_a_malformed_kek_is_reported_even_with_no_env_ring(
        self, monkeypatch
    ):
        # The ring may legitimately be empty in the environment once it lives in
        # the database, but a broken KEK still has to be caught.
        monkeypatch.setattr(settings, "NFC_MASTER_KEY", "")
        monkeypatch.setattr(settings, "NFC_KEK", "abcd")

        errors = settings.nfc_keyring_errors()

        assert any("NFC_KEK" in e for e in errors)

    def test_a_valid_kek_reports_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "NFC_KEK", KEK)

        assert settings.nfc_keyring_errors() == []


def test_a_concurrent_change_during_revocation_is_reported(
    with_kek, db_session, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    def _conflict():
        raise IntegrityError("stmt", {}, Exception("conflict"))

    monkeypatch.setattr(db_session, "commit", _conflict)

    # Two instances acting at once must surface as "retry", not a 500.
    with pytest.raises(ValueError, match="Another instance"):
        nfc_key_service.revoke_version(
            db_session, version=0, actor_id=None, reason="x"
        )

def test_lifespan_runs_without_a_kek(db_session, monkeypatch):
    """
    The app must boot with no KEK configured, which is how every environment
    starts out — merging this feature changes nothing until it is enabled.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    # Explicit, so the real lifespan runs here regardless of the local .env.
    monkeypatch.setattr(settings, "NFC_KEK", "")

    with TestClient(app) as c:
        assert c.get("/api/v1/patients/nonexistent-route").status_code == 404


def test_startup_helper_opens_and_closes_its_own_session(monkeypatch):
    from app.core import nfc_startup

    monkeypatch.setattr(settings, "NFC_KEK", "")
    # No KEK: the helper is a no-op and must not need a database at all.
    nfc_startup.prepare_nfc_keyring_at_startup()

def test_ensure_initialised_is_a_noop_without_a_kek(db_session):
    nfc_key_service.ensure_initialised(db_session)

    assert db_session.query(NfcKey).count() == 0

# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

ROTATE_URL = "/api/v1/patients/nfc-keys/rotate"


def _age_current_key(db, days: int):
    """Backdate the current key so the periodic check considers it stale."""
    from datetime import datetime, timedelta, timezone

    state = db.query(NfcKeyringState).filter(NfcKeyringState.id == 1).one()
    row = db.query(NfcKey).filter(NfcKey.version == state.current_version).one()
    row.created_at = datetime.now(timezone.utc) - timedelta(days=days)
    db.commit()
    nfc_key_service.invalidate_cache()


class TestManualRotation:
    def _rotate(self, client, token, ack=True, reason="quarterly rotation"):
        return client.post(
            ROTATE_URL,
            json={"reason": reason, "acknowledge_fleet_updated": ack},
            headers=_auth(token),
        )

    def test_creates_a_new_current_version(
        self, client, superadmin, with_kek, db_session
    ):
        resp = self._rotate(client, superadmin)

        assert resp.status_code == 200
        assert resp.json() == {"new_version": 1, "previous_version": 0}

    def test_the_previous_version_is_still_served(
        self, client, superadmin, with_kek, db_session
    ):
        self._rotate(client, superadmin)
        nfc_key_service.invalidate_cache()
        db_session.expire_all()

        claims = security.nfc_key_claims(role=UserRole.doctor, db=db_session)

        # Rotation is not revocation: chips on version 0 must stay readable
        # offline until they are rewritten.
        assert set(claims["nfc_keyring"]) == {"0", "1"}
        assert claims["nfc_key_version"] == 1

    def test_it_is_recorded(self, client, superadmin, with_kek, db_session):
        self._rotate(client, superadmin, reason="scheduled")
        db_session.expire_all()

        rotated = (
            db_session.query(NfcKeyEvent)
            .filter(NfcKeyEvent.action == "rotated")
            .one()
        )
        assert rotated.actor_id == "u-super"
        assert "scheduled" in rotated.reason

    def test_the_fleet_acknowledgement_is_required(
        self, client, superadmin, with_kek
    ):
        resp = self._rotate(client, superadmin, ack=False)

        assert resp.status_code == 422

    def test_a_doctor_cannot_rotate(self, client, doctor, with_kek):
        resp = self._rotate(client, doctor)

        assert resp.status_code == 403

    def test_without_a_kek_rotation_is_refused(self, client, superadmin):
        resp = self._rotate(client, superadmin)

        assert resp.status_code == 400


class TestAutomaticRotation:
    def test_disabled_by_default(self, with_kek, db_session):
        _age_current_key(db_session, days=999)

        # The flag is the fleet gate: it can never default to on.
        assert settings.NFC_AUTO_ROTATE is False
        assert nfc_key_service.maybe_auto_rotate(db_session) is None
        assert db_session.query(NfcKey).count() == 1

    def test_a_fresh_key_is_not_rotated(
        self, with_kek, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)

        assert nfc_key_service.maybe_auto_rotate(db_session) is None

    def test_a_stale_key_is_rotated(self, with_kek, db_session, monkeypatch):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)
        monkeypatch.setattr(settings, "NFC_ROTATION_PERIOD_DAYS", 90)
        _age_current_key(db_session, days=91)

        assert nfc_key_service.maybe_auto_rotate(db_session) == 1

        state = (
            db_session.query(NfcKeyringState)
            .filter(NfcKeyringState.id == 1)
            .one()
        )
        assert state.current_version == 1

    def test_the_period_is_configurable(
        self, with_kek, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)
        monkeypatch.setattr(settings, "NFC_ROTATION_PERIOD_DAYS", 30)
        _age_current_key(db_session, days=31)

        assert nfc_key_service.maybe_auto_rotate(db_session) == 1

    def test_an_automatic_rotation_records_no_actor(
        self, with_kek, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)
        _age_current_key(db_session, days=200)
        nfc_key_service.maybe_auto_rotate(db_session)
        db_session.expire_all()

        rotated = (
            db_session.query(NfcKeyEvent)
            .filter(NfcKeyEvent.action == "rotated")
            .one()
        )
        assert rotated.actor_id is None
        assert "automatic rotation" in rotated.reason

    def test_a_failure_never_propagates(
        self, with_kek, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)
        _age_current_key(db_session, days=200)

        def _boom(*args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(nfc_key_service, "rotate_to_new_version", _boom)

        # Taking down login because a rotation could not be written would be a
        # far worse trade than keeping the current key a little longer.
        assert nfc_key_service.maybe_auto_rotate(db_session) is None

    def test_nothing_happens_without_a_kek(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)

        assert nfc_key_service.maybe_auto_rotate(db_session) is None


class TestRotationLimits:
    def test_a_lost_race_is_not_an_error(
        self, with_kek, db_session, monkeypatch
    ):
        """
        Another instance advances the pointer between our read and our write.

        The conditional update must then match nothing and the call must return
        None: two Cloud Run instances reaching the same conclusion at the same
        moment have to end with one new current version, and the request that
        lost the race should be none the wiser.
        """
        from sqlalchemy.orm import Session as SASession

        original_insert = nfc_key_service._insert_key

        def _insert_then_lose_the_race(db, wrapper, version, material):
            original_insert(db, wrapper, version=version, material=material)
            # A competing instance commits its own advance first.
            other = SASession(bind=db_session.get_bind())
            try:
                other.query(NfcKeyringState).filter(
                    NfcKeyringState.id == 1
                ).update({"current_version": 77}, synchronize_session=False)
                other.commit()
            finally:
                other.close()

        monkeypatch.setattr(
            nfc_key_service, "_insert_key", _insert_then_lose_the_race
        )

        assert (
            nfc_key_service.rotate_to_new_version(
                db_session, actor_id=None, reason="race"
            )
            is None
        )

    def test_the_header_ceiling_is_enforced(
        self, client, superadmin, with_kek, db_session
    ):
        from app.core.key_wrapper import EnvKekWrapper, generate_key

        wrapper = EnvKekWrapper(KEK)
        db_session.add(
            NfcKey(
                version=255,
                wrapped_key=wrapper.wrap(generate_key(), 255),
                kek_id=wrapper.kek_id,
                status="live",
            )
        )
        db_session.commit()
        nfc_key_service.invalidate_cache()

        resp = client.post(
            ROTATE_URL,
            json={"reason": "ceiling test", "acknowledge_fleet_updated": True},
            headers=_auth(superadmin),
        )

        # The version travels in one byte of the payload header.
        assert resp.status_code == 400
        assert "255" in resp.json()["detail"]


class TestRotationFailureModes:
    def test_rotating_before_initialisation_is_refused(
        self, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "NFC_KEK", KEK)
        nfc_key_service.invalidate_cache()
        # KEK configured but ensure_initialised never ran.
        with pytest.raises(ValueError, match="not been initialised"):
            nfc_key_service.rotate_to_new_version(
                db_session, actor_id=None, reason="too early"
            )

    def test_a_commit_conflict_during_rotation_is_not_an_error(
        self, with_kek, db_session, monkeypatch
    ):
        from sqlalchemy.exc import IntegrityError

        def _conflict():
            raise IntegrityError("stmt", {}, Exception("conflict"))

        monkeypatch.setattr(db_session, "commit", _conflict)

        # Same meaning as losing the conditional update: another instance got
        # there first, and the caller carries on.
        assert (
            nfc_key_service.rotate_to_new_version(
                db_session, actor_id=None, reason="race at commit"
            )
            is None
        )

    def test_a_revoked_current_version_stops_auto_rotation(
        self, with_kek, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "NFC_AUTO_ROTATE", True)
        state = (
            db_session.query(NfcKeyringState)
            .filter(NfcKeyringState.id == 1)
            .one()
        )
        state.current_version = 404  # points at a version that does not exist
        db_session.commit()
        nfc_key_service.invalidate_cache()

        assert nfc_key_service.maybe_auto_rotate(db_session) is None
