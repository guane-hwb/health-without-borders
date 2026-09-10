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

from app.core import config, security
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


# ---------------------------------------------------------------------------
# Keyring validation and role gating
# ---------------------------------------------------------------------------

class TestKeyringValidation:
    def test_no_errors_when_unconfigured(self, clean_nfc_env):
        assert settings.nfc_keyring_errors() == []

    def test_no_errors_for_a_valid_ring(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V1)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 1)
        assert settings.nfc_keyring_errors() == []

    def test_malformed_key_is_reported_without_leaking_it(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setenv("NFC_KEY_V1", "not-a-hex-key")

        errors = settings.nfc_keyring_errors()

        assert any("NFC_KEY_V1" in e for e in errors)
        # The message names the variable, never the value.
        assert all("not-a-hex-key" not in e for e in errors)

    def test_key_of_wrong_length_is_reported(self, clean_nfc_env):
        clean_nfc_env.setenv("NFC_KEY_V1", "abcd")
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 1)
        assert any("NFC_KEY_V1" in e for e in settings.nfc_keyring_errors())

    def test_version_out_of_header_range_is_reported(self, clean_nfc_env):
        clean_nfc_env.setenv("NFC_KEY_V256", KEY_V1)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 256)
        assert any("NFC_KEY_V256" in e for e in settings.nfc_keyring_errors())

    def test_current_version_without_a_key_is_reported(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 3)
        assert any(
            "NFC_CURRENT_KEY_VERSION" in e for e in settings.nfc_keyring_errors()
        )


class TestRoleGating:
    def test_superadmin_gets_no_key_material(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)

        claims = security.nfc_key_claims(role=UserRole.superadmin)

        # A superadmin has no clinical access and never taps a wristband.
        assert claims == {
            "nfc_encryption_key": None,
            "nfc_key_version": None,
            "nfc_keyring": None,
        }

    def test_superadmin_gated_by_plain_string_too(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        assert security.nfc_key_claims(role="superadmin")["nfc_keyring"] is None

    def test_clinical_roles_still_get_the_ring(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)

        for role in (UserRole.doctor, UserRole.nurse, UserRole.org_admin):
            claims = security.nfc_key_claims(role=role)
            assert claims["nfc_keyring"] == {"0": KEY_V0}, role

    def test_omitting_role_still_delivers(self, clean_nfc_env):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        assert security.nfc_key_claims()["nfc_keyring"] == {"0": KEY_V0}

    def test_superadmin_login_carries_no_keyring(
        self, client: TestClient, db_session, clean_nfc_env
    ):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        org = _create_org(db_session)
        user = _create_user(db_session, org.id)
        user.role = UserRole.superadmin
        db_session.commit()

        data = _login(client)

        assert data["nfc_encryption_key"] is None
        assert data["nfc_key_version"] is None
        assert data["nfc_keyring"] is None


# ---------------------------------------------------------------------------
# Keys declared in a .env file (local development)
# ---------------------------------------------------------------------------

class TestDotenvKeys:
    """
    ``NFC_KEY_V<n>`` names are dynamic, so pydantic-settings never loads them
    from ``.env`` into a field. They are read from the file explicitly, or a
    rotation a developer configures locally would be silently ignored while the
    same variable works in production.
    """

    def _write_env(self, tmp_path, monkeypatch, body: str):
        env_file = tmp_path / ".env"
        env_file.write_text(body, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        return env_file

    def test_versioned_key_is_read_from_dotenv(
        self, clean_nfc_env, tmp_path
    ):
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)
        self._write_env(tmp_path, clean_nfc_env, f'NFC_KEY_V1="{KEY_V1}"\n')

        assert settings.nfc_keyring() == {0: KEY_V0, 1: KEY_V1}

    def test_process_environment_wins_over_dotenv(
        self, clean_nfc_env, tmp_path
    ):
        self._write_env(tmp_path, clean_nfc_env, f'NFC_KEY_V1="{KEY_V1}"\n')
        clean_nfc_env.setenv("NFC_KEY_V1", KEY_V2)

        # Matches how every other setting behaves in a deployment.
        assert settings.nfc_keyring() == {1: KEY_V2}

    def test_missing_dotenv_is_not_an_error(self, clean_nfc_env, tmp_path):
        clean_nfc_env.chdir(tmp_path)
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)

        assert settings.nfc_keyring() == {0: KEY_V0}

    def test_unreadable_dotenv_is_ignored_not_fatal(
        self, clean_nfc_env, tmp_path
    ):
        self._write_env(tmp_path, clean_nfc_env, f'NFC_KEY_V1="{KEY_V1}"\n')
        clean_nfc_env.setattr(settings, "NFC_MASTER_KEY", KEY_V0)

        def _boom(*args, **kwargs):
            raise OSError("permission denied")

        clean_nfc_env.setattr(config, "dotenv_values", _boom)

        # A .env that cannot be read must not take the app down: in a
        # deployment the process environment is the authoritative source.
        assert settings.nfc_keyring() == {0: KEY_V0}

    def test_dotenv_keys_are_validated_like_any_other(
        self, clean_nfc_env, tmp_path
    ):
        self._write_env(tmp_path, clean_nfc_env, 'NFC_KEY_V1="not-hex"\n')
        clean_nfc_env.setattr(settings, "NFC_CURRENT_KEY_VERSION", 1)

        errors = settings.nfc_keyring_errors()

        assert any("NFC_KEY_V1" in e for e in errors)
        assert all("not-hex" not in e for e in errors)
