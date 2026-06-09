"""
Tests for the authentication endpoints.

Covers:
- POST /api/v1/login/access-token (login)
- POST /api/v1/login/refresh (H4: token rotation)
- POST /api/v1/logout (H4: token revocation)
- JWT protection on protected endpoints
"""
from fastapi.testclient import TestClient

from app.core.security import get_password_hash
from app.db.models import Organization, User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_org(db, name: str = "Test Clinic") -> Organization:
    org = Organization(id="org-login-test", name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _create_user(db, org_id: str, email: str, password: str, is_active: bool = True) -> User:
    user = User(
        id="user-login-test",
        organization_id=org_id,
        full_name="Doctor Login Test",
        email=email,
        hashed_password=get_password_hash(password),
        role=UserRole.doctor,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, email="doctor@hwb.org", password="SecurePass123"):
    """Helper: log in and return the full response JSON."""
    return client.post(
        "/api/v1/login/access-token",
        data={"username": email, "password": password},
    )


# ---------------------------------------------------------------------------
# Tests: POST /api/v1/login/access-token
# ---------------------------------------------------------------------------

class TestLoginSuccess:
    def test_returns_token_pair(self, client: TestClient, db_session):
        """H4: A valid login returns access_token + refresh_token + expires_in."""
        org = _create_org(db_session)
        _create_user(db_session, org.id, "doctor@hwb.org", "SecurePass123")

        response = _login(client)

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0
        assert len(data["access_token"]) > 20
        assert len(data["refresh_token"]) > 20


class TestLoginFailures:
    def test_wrong_password_returns_401(self, client: TestClient, db_session):
        org = _create_org(db_session)
        _create_user(db_session, org.id, "doctor@hwb.org", "CorrectPassword")

        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "doctor@hwb.org", "password": "WrongPassword"},
        )

        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    def test_nonexistent_user_returns_401(self, client: TestClient, db_session):
        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "ghost@hwb.org", "password": "AnyPassword"},
        )

        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    def test_inactive_user_returns_400(self, client: TestClient, db_session):
        org = _create_org(db_session)
        _create_user(db_session, org.id, "inactive@hwb.org", "ValidPass123", is_active=False)

        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "inactive@hwb.org", "password": "ValidPass123"},
        )

        assert response.status_code == 400
        assert "Inactive user" in response.json()["detail"]

    def test_empty_credentials_returns_422(self, client: TestClient, db_session):
        response = client.post("/api/v1/login/access-token", data={})
        assert response.status_code == 422

    def test_wrong_password_does_not_leak_user_existence(self, client: TestClient, db_session):
        org = _create_org(db_session)
        _create_user(db_session, org.id, "real@hwb.org", "CorrectPassword")

        response_wrong_pass = client.post(
            "/api/v1/login/access-token",
            data={"username": "real@hwb.org", "password": "Wrong"},
        )
        response_no_user = client.post(
            "/api/v1/login/access-token",
            data={"username": "fake@hwb.org", "password": "Wrong"},
        )

        assert response_wrong_pass.json()["detail"] == response_no_user.json()["detail"]


# ---------------------------------------------------------------------------
# H4: Tests for token refresh rotation
# ---------------------------------------------------------------------------

class TestTokenRefresh:
    def _setup_and_login(self, client, db_session):
        org = _create_org(db_session)
        _create_user(db_session, org.id, "doctor@hwb.org", "SecurePass123")
        resp = _login(client)
        return resp.json()

    def test_refresh_returns_new_token_pair(self, client: TestClient, db_session):
        """H4: A valid refresh token exchanges for a new access + refresh pair."""
        tokens = self._setup_and_login(client, db_session)

        response = client.post(
            "/api/v1/login/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New tokens must be different from the originals (rotation)
        assert data["access_token"] != tokens["access_token"]
        assert data["refresh_token"] != tokens["refresh_token"]

    def test_refresh_revokes_old_token(self, client: TestClient, db_session):
        """H4: After refresh, the old refresh token cannot be reused."""
        tokens = self._setup_and_login(client, db_session)
        old_refresh = tokens["refresh_token"]

        # First refresh succeeds
        client.post("/api/v1/login/refresh", json={"refresh_token": old_refresh})

        # Second attempt with same token fails (revoked)
        response = client.post(
            "/api/v1/login/refresh",
            json={"refresh_token": old_refresh},
        )
        assert response.status_code == 401

    def test_refresh_rejects_access_token(self, client: TestClient, db_session):
        """H4: An access token cannot be used as a refresh token."""
        tokens = self._setup_and_login(client, db_session)

        response = client.post(
            "/api/v1/login/refresh",
            json={"refresh_token": tokens["access_token"]},  # wrong token type
        )
        assert response.status_code == 401

    def test_refresh_rejects_garbage(self, client: TestClient, db_session):
        """H4: Random strings are rejected."""
        response = client.post(
            "/api/v1/login/refresh",
            json={"refresh_token": "not.a.jwt"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# H4: Tests for logout / token revocation
# ---------------------------------------------------------------------------

class TestLogout:
    def _setup_and_login(self, client, db_session):
        org = _create_org(db_session)
        # Use org_admin role because /api/v1/users/ requires org_admin or superadmin
        user = User(
            id="user-logout-test",
            organization_id=org.id,
            full_name="Admin Logout Test",
            email="admin-logout@hwb.org",
            hashed_password=get_password_hash("SecurePass123"),
            role=UserRole.org_admin,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        resp = _login(client, email="admin-logout@hwb.org", password="SecurePass123")
        return resp.json()

    def test_logout_revokes_access_token(self, client: TestClient, db_session):
        """H4: After logout, the access token is rejected on subsequent requests."""
        tokens = self._setup_and_login(client, db_session)
        access_token = tokens["access_token"]

        # Access works before logout
        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200

        # Logout
        response = client.post(
            "/api/v1/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 204

        # Access token is now revoked
        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 401
        assert "revoked" in response.json()["detail"].lower()

    def test_logout_is_idempotent(self, client: TestClient, db_session):
        """H4: Logging out twice with the same token returns 204 both times."""
        tokens = self._setup_and_login(client, db_session)

        for _ in range(2):
            response = client.post(
                "/api/v1/logout",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            assert response.status_code == 204


# ---------------------------------------------------------------------------
# Tests: Protected endpoint behaviour with JWT
# ---------------------------------------------------------------------------

class TestJWTProtection:
    def _get_token(self, client: TestClient, db_session, role: UserRole = UserRole.org_admin) -> str:
        org = _create_org(db_session)
        user = User(
            id="user-jwt-test",
            organization_id=org.id,
            full_name="JWT Tester",
            email="jwt@hwb.org",
            hashed_password=get_password_hash("JwtPass123"),
            role=role,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "jwt@hwb.org", "password": "JwtPass123"},
        )
        return response.json()["access_token"]

    def test_valid_token_grants_access(self, client: TestClient, db_session):
        token = self._get_token(client, db_session)

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

    def test_missing_token_returns_401(self, client: TestClient, db_session):
        response = client.get("/api/v1/users/")
        assert response.status_code == 401

    def test_malformed_token_returns_401(self, client: TestClient, db_session):
        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
        )
        assert response.status_code == 401

    def test_wrong_scheme_returns_401(self, client: TestClient, db_session):
        token = self._get_token(client, db_session)

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Basic {token}"},
        )
        assert response.status_code == 401

    def test_tampered_token_returns_401(self, client: TestClient, db_session):
        token = self._get_token(client, db_session)
        header, payload, _ = token.rsplit(".", 2)
        fake_signature = "aW52YWxpZHNpZ25hdHVyZWZvcnRlc3Rpbmcx"
        tampered_token = f"{header}.{payload}.{fake_signature}"

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert response.status_code == 401

    def test_refresh_token_rejected_on_protected_endpoint(self, client: TestClient, db_session):
        """H4: A refresh token cannot be used to access protected API endpoints."""
        org = _create_org(db_session)
        _create_user(db_session, org.id, "doctor@hwb.org", "SecurePass123")
        tokens = _login(client).json()

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        assert response.status_code == 401
