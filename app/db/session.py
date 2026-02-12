from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

def get_database_uri() -> str:
    """
    Constructs the database connection URI based on the execution environment.
    
    Priority 1: Cloud Run (Unix Socket).
        If 'INSTANCE_CONNECTION_NAME' is present, we assume we are running in GCP 
        (or using the Auth Proxy) and must connect via the Unix socket.
        
    Priority 2: Local Development (Explicit URL).
        If 'DATABASE_URL' is provided in .env, use it directly.
        
    Priority 3: Local Fallback (TCP).
        Constructs a standard localhost connection string.
    """
    
    # Strategy 1: Google Cloud SQL (Unix Socket)
    if settings.INSTANCE_CONNECTION_NAME:
        db_user = settings.DB_USER
        db_pass = settings.DB_PASS
        db_name = settings.DB_NAME
        instance_connection_name = settings.INSTANCE_CONNECTION_NAME
        
        # Google Cloud SQL Proxy mounts the socket at /cloudsql/<INSTANCE_NAME>
        socket_path = f"/cloudsql/{instance_connection_name}"
        
        # The 'postgresql+psycopg2' driver requires this specific format for sockets
        return f"postgresql+psycopg2://{db_user}:{db_pass}@/{db_name}?host={socket_path}"

    # Strategy 2: Explicit Local URL (e.g., from .env)
    if settings.DATABASE_URL:
        return settings.DATABASE_URL
        
    # Strategy 3: Default Localhost TCP Fallback
    return f"postgresql://{settings.DB_USER}:{settings.DB_PASS}@localhost:5432/{settings.DB_NAME}"


# Generate the connection string
SQLALCHEMY_DATABASE_URI = get_database_uri()

# Create the SQLAlchemy Engine
# pool_pre_ping=True: Essential for Cloud SQL. It checks if the connection is alive 
# before using it, preventing "server closed the connection unexpectedly" errors.
engine = create_engine(
    SQLALCHEMY_DATABASE_URI, 
    pool_pre_ping=True
)

# Create the SessionLocal class
# autocommit=False: We manually commit to ensure transaction integrity.
# autoflush=False: We manually flush to control when SQL is sent to the DB.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """
    FastAPI Dependency for Database Sessions.
    
    Yields a database session for the request scope and ensures it is closed 
    after the request is finished, returning the connection to the pool.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()