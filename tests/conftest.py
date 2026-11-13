import logging
import os
from typing import Generator
from urllib.parse import urlsplit

# Tests never talk to Google. A developer's .env usually selects the real FHIR
# Store and Gemini, and Python libraries authenticate with whatever
# application-default credentials the machine holds: a local test run sent
# synthetic bundles to the pilot's FHIR Store (refused only because those
# credentials had no access, 2026-11-12). Environment variables win over .env,
# and they must be set before the application is imported, because the
# backends are built at import time.
os.environ["FHIR_BACKEND"] = "noop"
os.environ["LLM_BACKEND"] = "noop"

import httpx  # noqa: E402
import pytest  # noqa: E402
import requests.adapters  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

# Second line of defence, for a test that builds a real backend itself: every
# request to a Google host is refused and recorded, and the test that made it
# fails. Refusing alone is not enough: the FHIR backend catches transport
# errors and reports "error", so the test could still pass.
GOOGLE_HOSTS = ("googleapis.com", "metadata.google.internal", "169.254.169.254")
BLOCKED_GOOGLE_CALLS: list[str] = []


def _is_google(url: object) -> bool:
    host = urlsplit(str(url)).hostname or ""
    return any(host == h or host.endswith("." + h) for h in GOOGLE_HOSTS)


def _refuse(url: object) -> None:
    BLOCKED_GOOGLE_CALLS.append(urlsplit(str(url)).hostname or "")
    raise ConnectionError(f"Tests must not call Google: {urlsplit(str(url)).hostname}")


_requests_send = requests.adapters.HTTPAdapter.send
_httpx_send = httpx.HTTPTransport.handle_request
_httpx_async_send = httpx.AsyncHTTPTransport.handle_async_request


def _guarded_requests_send(self, request, *args, **kwargs):
    if _is_google(request.url):
        _refuse(request.url)
    return _requests_send(self, request, *args, **kwargs)


def _guarded_httpx_send(self, request):
    if _is_google(request.url):
        _refuse(request.url)
    return _httpx_send(self, request)


async def _guarded_httpx_async_send(self, request):
    if _is_google(request.url):
        _refuse(request.url)
    return await _httpx_async_send(self, request)


requests.adapters.HTTPAdapter.send = _guarded_requests_send
httpx.HTTPTransport.handle_request = _guarded_httpx_send
httpx.AsyncHTTPTransport.handle_async_request = _guarded_httpx_async_send


@pytest.fixture(autouse=True)
def no_google_calls() -> Generator[None, None, None]:
    BLOCKED_GOOGLE_CALLS.clear()
    yield
    if BLOCKED_GOOGLE_CALLS:
        hosts = sorted(set(BLOCKED_GOOGLE_CALLS))
        BLOCKED_GOOGLE_CALLS.clear()
        pytest.fail(f"This test tried to reach Google ({', '.join(hosts)}); mock the backend.")

# Use SQLite in-memory for fast testing without touching the real Postgres DB.
# check_same_thread=False is required because FastAPI runs on multiple threads, 
# but SQLite usually restricts connection to a single thread.
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False}, 
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def reset_dependency_overrides() -> Generator[None, None, None]:
    """
    Guarantee that FastAPI dependency overrides set by any test are removed
    once the test finishes, even if it fails mid-way. Runs for every test, so
    individual tests no longer need manual cleanup that can be skipped on error.
    """
    yield
    app.dependency_overrides.clear()

@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """
    Fixture that creates a fresh database session for each test function.
    """
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    """
    Fixture that returns a FastAPI TestClient with the database dependency
    overridden and rate limiter reset for test isolation.
    """
    # Startup seeds the NFC keyring through SessionLocal — the REAL database
    # from the developer's .env, not the in-memory one below. Tests must never
    # depend on it being reachable, so the step is skipped here; it is covered
    # directly in test_nfc_keys.py.
    monkeypatch.setattr("app.main.prepare_nfc_keyring_at_startup", lambda: None)
    monkeypatch.setattr("app.main.report_schema_drift_at_startup", lambda: None)
    monkeypatch.setattr("app.main.run_migrations_at_startup", lambda: None)

    def override_get_db():
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db] = override_get_db

    # Reset rate limiter storage between tests to prevent cross-test 429s
    from app.core.rate_limit import limiter
    try:
        limiter.reset()
    except Exception:
        pass  # In-memory storage may not support reset — it's recreated anyway

    with TestClient(app) as c:
        yield c


@pytest.fixture
def app_log(caplog):
    """The "app" logger does not propagate to root, so hook caplog in directly."""
    logger = logging.getLogger("app")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="app")
    yield caplog
    logger.removeHandler(caplog.handler)
