"""
PostgreSQL integration tests.

They run the real application — its startup (migrations, schema check, NFC
keyring), its own database session, real users and real tokens, no
dependency_overrides — against the PostgreSQL database named in DATABASE_URL.
Without a PostgreSQL DATABASE_URL every test here is skipped, so the regular
(SQLite) suite is unaffected. CI runs them in the "integration" job.

Each test starts from an empty schema at the latest migration. Because that
means DROP SCHEMA on the target database, the database name must contain
"test" (e.g. hwb_test); anything else aborts the run before touching it.
"""
import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

PG_URL = os.environ.get("DATABASE_URL", "")
IS_POSTGRES = PG_URL.startswith("postgresql")

PASSWORD = "IntegrationPass2026!"


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="needs a PostgreSQL DATABASE_URL")
    for item in items:
        if "tests/integration/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.pg)
            if not IS_POSTGRES:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def pg_engine():
    from app.db.migrations import upgrade_to_head
    from app.db.session import engine

    # The engine may come from .env, not only from DATABASE_URL: check the URL
    # it actually uses before dropping anything.
    if engine.dialect.name != "postgresql" or "test" not in (engine.url.database or ""):
        pytest.exit(
            f"Refusing to reset database {engine.url.database!r}: integration tests "
            "need a PostgreSQL database whose name contains 'test'.",
            returncode=2,
        )
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    upgrade_to_head(engine)
    return engine


@pytest.fixture(autouse=True)
def clean_database(request, pg_engine) -> Iterator[None]:
    """Empty every table (except Alembic's) before each test."""
    from app.db.base import Base

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with pg_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    from app.core.rate_limit import limiter
    from app.services import nfc_key_service

    limiter.reset()
    nfc_key_service.invalidate_cache()
    yield


@pytest.fixture
def api() -> Iterator[TestClient]:
    """The real app, real lifespan, real database session."""
    from app.main import app

    app.dependency_overrides.clear()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_org(db, name: str) -> str:
    from app.db.models import Organization

    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    return org.id


def make_user(db, org_id: str, email: str, role) -> str:
    from app.core.security import get_password_hash
    from app.db.models import User

    user = User(
        email=email, full_name="Persona Sintetica", hashed_password=get_password_hash(PASSWORD),
        role=role, is_active=True, organization_id=org_id,
    )
    db.add(user)
    db.commit()
    return user.id


def login(api: TestClient, email: str) -> dict:
    response = api.post("/api/v1/login/access-token", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    tokens = response.json()
    tokens["headers"] = {"Authorization": f"Bearer {tokens['access_token']}"}
    return tokens


@pytest.fixture
def staff(api, db):
    """One organization with an admin, a doctor and a nurse; a second one; a superadmin."""
    from app.db.models import UserRole

    org_a = make_org(db, "Org A")
    org_b = make_org(db, "Org B")
    hq = make_org(db, "HQ")
    ids = {
        "org_a": org_a, "org_b": org_b,
        "admin_a": make_user(db, org_a, "admin@a.org", UserRole.org_admin),
        "doc_a": make_user(db, org_a, "doc@a.org", UserRole.doctor),
        "nurse_a": make_user(db, org_a, "nurse@a.org", UserRole.nurse),
        "doc_b": make_user(db, org_b, "doc@b.org", UserRole.doctor),
        "sa": make_user(db, hq, "sa@hq.org", UserRole.superadmin),
    }
    tokens = {
        who: login(api, email) for who, email in (
            ("admin_a", "admin@a.org"), ("doc_a", "doc@a.org"), ("nurse_a", "nurse@a.org"),
            ("doc_b", "doc@b.org"), ("sa", "sa@hq.org"),
        )
    }
    return {"ids": ids, "tokens": tokens}
