from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# pool_pre_ping=True helps handle DB connection drops gracefully
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# Create SessionLocal class
# autocommit=False: We want to control when to commit transactions
# autoflush=False: We want to control when changes are sent to SQL
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """
    Dependency generator for FastAPI.
    Creates a new database session for each request and closes it afterwards.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()