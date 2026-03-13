# tests/conftest.py
import pytest
from typing import Generator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.main import app
from app.db.session import get_db

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

@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """
    Fixture that creates a fresh database session for each test function.
    
    Strategy:
    1. Create all tables in the in-memory SQLite database.
    2. Yield the session to the test.
    3. Drop all tables after the test finishes to ensure a clean state.
    """
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        # Drop tables to cleanup
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """
    Fixture that returns a FastAPI TestClient with the database dependency overridden.
    
    This ensures that when the API calls `get_db`, it receives our 
    test database session instead of the production PostgreSQL session.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            db_session.close()

    # Override the dependency
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as c:
        yield c