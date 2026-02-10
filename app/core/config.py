# app/core/config.py
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "UNICEF Border Health Backend"
    API_V1_STR: str = "/api/v1"
    
    # Database
    # Use PostgresDsn for strict validation in production
    DATABASE_URL: str 

    # Google Cloud Platform
    GCP_PROJECT_ID: str
    GCP_LOCATION: str
    GCP_DATASET_ID: str
    GCP_HL7_STORE_ID: str
    GOOGLE_APPLICATION_CREDENTIALS: str

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    DEBUG: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()