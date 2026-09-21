from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app

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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """
    Fixture that returns a FastAPI TestClient with the database dependency
    overridden and rate limiter reset for test isolation.
    """
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
