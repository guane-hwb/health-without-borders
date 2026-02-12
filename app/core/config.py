import os
from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Application Configuration.
    Reads settings from environment variables or .env file.
    """
    PROJECT_NAME: str = "UNICEF Border Health Backend"
    API_V1_STR: str = "/api/v1"

    # --- DATABASE CONFIGURATION ---
    # We allow individual components to support Cloud SQL socket connections.
    DB_USER: str = "postgres"
    DB_PASS: str = "password"
    DB_NAME: str = "postgres"
    
    # This variable is automatically injected by Google Cloud Run when using 
    # the flag --add-cloudsql-instances. It triggers the Unix Socket connection strategy.
    INSTANCE_CONNECTION_NAME: Optional[str] = None 

    # Fallback for local development (standard TCP connection string)
    DATABASE_URL: Optional[str] = None

    # --- GOOGLE CLOUD PLATFORM ---
    GCP_PROJECT_ID: Optional[str] = None
    GCP_LOCATION: Optional[str] = None
    GCP_DATASET_ID: Optional[str] = None
    GCP_HL7_STORE_ID: Optional[str] = None
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None

    # --- SECURITY ---
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440 # 24 hours
    
    DEBUG: bool = False

    class Config:
        case_sensitive = True
        env_file = ".env"
        # "ignore" allows extra variables in the .env file without throwing validation errors
        extra = "ignore" 

settings = Settings()