"""
Tests for the authentication endpoint: POST /api/v1/login/access-token

Covers:
- Successful login returns a valid JWT
- Wrong password returns 401
- Non-existent user returns 401
- Inactive user returns 400
- Accessing a protected endpoint with a valid token returns 200
- Accessing a protected endpoint without a token returns 401
- Accessing a protected endpoint with a malformed token returns 401
"""
from fastapi.testclient import TestClient

from app.core.security import get_password_hash
from app.db.models import Organization, User


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
        role="doctor",
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Tests: POST /api/v1/login/access-token
# ---------------------------------------------------------------------------

class TestLoginSuccess:
    def test_returns_access_token_and_bearer_type(self, client: TestClient, db_session):
        """A valid email/password pair returns a JWT with token_type 'bearer'."""
        org = _create_org(db_session)
        _create_user(db_session, org.id, "doctor@hwb.org", "SecurePass123")

        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "doctor@hwb.org", "password": "SecurePass123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert len(data["access_token"]) > 20  # non-empty JWT


class TestLoginFailures:
    def test_wrong_password_returns_401(self, client: TestClient, db_session):
        """A correct email but wrong password must return 401, not 403 or 400."""
        org = _create_org(db_session)
        _create_user(db_session, org.id, "doctor@hwb.org", "CorrectPassword")

        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "doctor@hwb.org", "password": "WrongPassword"},
        )

        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    def test_nonexistent_user_returns_401(self, client: TestClient, db_session):
        """An email that does not exist in the DB must return 401."""
        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "ghost@hwb.org", "password": "AnyPassword"},
        )

        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    def test_inactive_user_returns_400(self, client: TestClient, db_session):
        """A valid password for a deactivated account must return 400."""
        org = _create_org(db_session)
        _create_user(db_session, org.id, "inactive@hwb.org", "ValidPass123", is_active=False)

        response = client.post(
            "/api/v1/login/access-token",
            data={"username": "inactive@hwb.org", "password": "ValidPass123"},
        )

        assert response.status_code == 400
        assert "Inactive user" in response.json()["detail"]

    def test_empty_credentials_returns_422(self, client: TestClient, db_session):
        """Missing form fields must return 422 Unprocessable Entity (FastAPI validation)."""
        response = client.post("/api/v1/login/access-token", data={})

        assert response.status_code == 422

    def test_wrong_password_does_not_leak_user_existence(self, client: TestClient, db_session):
        """
        The error message for wrong password and non-existent user must be identical
        to prevent user enumeration attacks.
        """
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
# Tests: Protected endpoint behaviour with JWT
# ---------------------------------------------------------------------------

class TestJWTProtection:
    """
    Uses GET /api/v1/users/ as the representative protected endpoint.
    These tests validate that the JWT middleware works correctly end-to-end.
    """

    def _get_token(self, client: TestClient, db_session, role: str = "org_admin") -> str:
        """Helper: create a user, log in, and return the raw access token string."""
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
        """A valid JWT in the Authorization header allows access to protected routes."""
        token = self._get_token(client, db_session)

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200

    def test_missing_token_returns_401(self, client: TestClient, db_session):
        """Accessing a protected route without Authorization header returns 401."""
        response = client.get("/api/v1/users/")

        assert response.status_code == 401

    def test_malformed_token_returns_401(self, client: TestClient, db_session):
        """A token that is not a valid JWT returns 401."""
        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
        )

        assert response.status_code == 401

    def test_wrong_scheme_returns_401(self, client: TestClient, db_session):
        """Using 'Basic' instead of 'Bearer' as the auth scheme returns 401."""
        token = self._get_token(client, db_session)

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Basic {token}"},
        )

        assert response.status_code == 401

    def test_tampered_token_returns_401(self, client: TestClient, db_session):
        """
        A JWT whose signature has been tampered with must be rejected.
        We flip one character in the signature portion (last segment).
        """
        token = self._get_token(client, db_session)
        header, payload, signature = token.rsplit(".", 2)
        # Flip the last character of the signature
        bad_char = "A" if signature[-1] != "A" else "B"
        tampered_token = f"{header}.{payload}.{signature[:-1]}{bad_char}"

        response = client.get(
            "/api/v1/users/",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )

        assert response.status_code == 401
