"""
Tests for NFC key versioning (keyring delivery).

Covers:
- Settings.nfc_keyring(): legacy master key, versioned NFC_KEY_V<n> vars, merge.
- security.nfc_key_claims(): backward compatibility, rotation, no-key, and the
  misconfigured "current version missing from ring" case.
- Delivery of the keyring in the login, refresh, and /users/me responses.
"""
import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.models import Organization, User, UserRole

# A pair of distinct, well-formed 64-hex-char (32-byte) AES-256 keys.
KEY_V0 = "00" * 32
KEY_V1 = "11" * 32
KEY_V2 = "22" * 32


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _create_org(db, name: str = "Keyring Clinic") -> Organization:
    org = Organization(id="org-keyring-test", name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _create_user(db, org_id: str) -> User:
    user = User(
        id="user-keyring-test",
        organization_id=org_id,
        full_name="Doctor Keyring Test",
        email="keyring@hwb.org",
        hashed_password=get_password_hash("SecurePass123"),
        role=UserRole.doctor,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client) -> dict:
    resp = client.post(
        "/api/v1/login/access-token",
        data={"username": "keyring@hwb.org", "password": "SecurePass123"},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture
def clean_nfc_env(monkeypatch):
    """
    Isolate NFC key configuration for a test: clear any NFC_KEY_V<n> vars from
    the process environment and reset the legacy key and current version to
    known defaults. Individual tests then set exactly what they need.
    """
    import os

    for name in list(os.environ):
        if name.startswith("NFC_KEY_V"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "NFC_MASTER_KEY", "")
    monkeypatch.setattr(settings, "NFC_CURRENT_KEY_VERSION", 0)
    yield monkeypatch


# ---------------------------------------------------------------------------
# Settings.nfc_keyring()
# ---------------------------------------------------------------------------

class TestKeyring:
    def test_empty_when_nothing_configured(self, clean_nfc_env):
        assert settings.nfc_keyring() == {}

    def test_legacy_master_is_version_zero(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        assert settings.nfc_keyring() == {0: KEY_V0}

    def test_versioned_env_vars_registered(self, clean_nfc_env):
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        clean_nfc_env.setenv("NFC_KEY_V2", KEY_V2)
        assert settings.nfc_keyring() == {1: KEY_V1, 2: KEY_V2}

    def test_master_and_versioned_merge(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        assert settings.nfc_keyring() == {0: KEY_V0, 1: KEY_V1}

    def test_blank_values_skipped(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", "   ")
        clean_nfc_env.setenv("NFC_KEY_V1", "  ")
        clean_nfc_env.setenv("NFC_KEY_V2", KEY_V2)
        assert settings.nfc_keyring() == {2: KEY_V2}

    def test_non_matching_env_ignored(self, clean_nfc_env):
        clean_nfc_env.setenv("NFC_KEY_VX", KEY_V1)  # not \\d+
        clean_nfc_env.setenv("NFC_KEYRING", KEY_V2)  # different name
        assert settings.nfc_keyring() == {}


# ---------------------------------------------------------------------------
# security.nfc_key_claims()
# ---------------------------------------------------------------------------

class TestNfcKeyClaims:
    def test_no_key_returns_all_none(self, clean_nfc_env):
        assert security.nfc_key_claims() == {
            "nfc_encryption_key": None,
            "nfc_key_version": None,
            "nfc_keyring": None,
        }

    def test_backward_compatible_master_only(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        claims = security.nfc_key_claims()
        assert claims["nfc_encryption_key"] == KEY_V0
        assert claims["nfc_key_version"] == 0
        assert claims["nfc_keyring"] == {"0": KEY_V0}

    def test_rotated_current_version(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        clean_nfc_env.setenv("NFC_KEY_V2", KEY_V2)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 2)
        claims = security.nfc_key_claims()
        assert claims["nfc_encryption_key"] == KEY_V2
        assert claims["nfc_key_version"] == 2
        # Every live version is delivered so older tags stay readable offline.
        assert claims["nfc_keyring"] == {"0": KEY_V0, "1": KEY_V1, "2": KEY_V2}

    def test_current_version_missing_from_ring(self, clean_nfc_env):
        # Only version 1 exists but current points at 2: readable, not writable.
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 2)
        claims = security.nfc_key_claims()
        assert claims["nfc_encryption_key"] is None
        assert claims["nfc_key_version"] is None
        assert claims["nfc_keyring"] == {"1": KEY_V1}


# ---------------------------------------------------------------------------
# Endpoint delivery: login, refresh, /users/me
# ---------------------------------------------------------------------------

class TestEndpointDelivery:
    def test_login_delivers_keyring(self, client: TestClient, db_session, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 1)
        org = _create_org(db_session)
        _create_user(db_session, org.id)

        data = _login(client)

        assert data["nfc_encryption_key"] == KEY_V1
        assert data["nfc_key_version"] == 1
        assert data["nfc_keyring"] == {"0": KEY_V0, "1": KEY_V1}

    def test_refresh_delivers_keyring(self, client: TestClient, db_session, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 1)
        org = _create_org(db_session)
        _create_user(db_session, org.id)
        tokens = _login(client)

        resp = client.post(
            "/api/v1/login/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        # DP3: the keyring rides on refresh so a cold-started client repopulates
        # its in-memory key without an extra /users/me call.
        assert data["nfc_encryption_key"] == KEY_V1
        assert data["nfc_key_version"] == 1
        assert data["nfc_keyring"] == {"0": KEY_V0, "1": KEY_V1}

    def test_me_delivers_keyring(self, client: TestClient, db_session, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 0)
        org = _create_org(db_session)
        _create_user(db_session, org.id)
        tokens = _login(client)

        resp = client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["nfc_encryption_key"] == KEY_V0
        assert data["nfc_key_version"] == 0
        assert data["nfc_keyring"] == {"0": KEY_V0}

    def test_login_omits_key_when_unconfigured(
        self, client: TestClient, db_session, clean_nfc_env
    ):
        org = _create_org(db_session)
        _create_user(db_session, org.id)

        data = _login(client)

        assert data["nfc_encryption_key"] is None
        assert data["nfc_key_version"] is None
        assert data["nfc_keyring"] is None
