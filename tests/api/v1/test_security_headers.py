"""
Security headers (audit be-v2-docs-openapi-publicos-sin-cabeceras, headers part).
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.security_headers import SecurityHeadersMiddleware
from app.main import app
from tests.api.v1.test_patients import MockUser

HARDENING = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=31536000",
}


def _assert_hardened(response):
    for name, value in HARDENING.items():
        assert response.headers.get(name) == value, name


def test_api_responses_are_never_cached(client: TestClient):
    app.dependency_overrides[get_current_user] = lambda: MockUser()

    ok = client.get("/api/v1/patients/scan/NO-EXISTE")
    denied = client.post("/api/v1/login/access-token", data={"username": "x@y.org", "password": "z"})

    for response in (ok, denied):
        assert response.headers["cache-control"] == "no-store"
        _assert_hardened(response)


def test_rejected_oversized_bodies_carry_the_headers_too(client: TestClient):
    response = client.post(
        "/api/v1/login/access-token", content=b"a;" * 20_000,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"
    _assert_hardened(response)


def test_non_api_routes_are_hardened_but_cacheable(client: TestClient):
    response = client.get("/health-check")

    _assert_hardened(response)
    assert "cache-control" not in response.headers


@pytest.mark.anyio
async def test_existing_headers_are_kept():
    async def app_with_own_policy(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"cache-control", b"max-age=60"), (b"x-frame-options", b"SAMEORIGIN"),
        ]})
        await send({"type": "http.response.body", "body": b"{}"})

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():  # pragma: no cover - never called
        return {"type": "http.request"}

    await SecurityHeadersMiddleware(app_with_own_policy)(
        {"type": "http", "path": "/api/v1/x", "headers": []}, receive, send
    )

    headers = dict(sent[0]["headers"])
    assert headers[b"cache-control"] == b"max-age=60"
    assert headers[b"x-frame-options"] == b"SAMEORIGIN"
    assert headers[b"x-content-type-options"] == b"nosniff"
