from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Configuration.
    Reads settings from environment variables or .env file.
    """
    PROJECT_NAME: str = "Health Without Borders API"
    API_V1_STR: str = "/api/v1"

    # --- INITIAL SUPERUSER ---
    FIRST_SUPERUSER_EMAIL: Optional[str] = None
    FIRST_SUPERUSER_PASSWORD: Optional[str] = None
    ROOT_ORGANIZATION_NAME: str = "Guane"
    
    # --- DATABASE CONFIGURATION ---
    # We allow individual components to support Cloud SQL socket connections.
    DB_USER: Optional[str] = None 
    DB_PASS: Optional[str] = None 
    DB_NAME: Optional[str] = None 
    
    # This variable is automatically injected by Google Cloud Run when using 
    # the flag --add-cloudsql-instances. It triggers the Unix Socket connection strategy.
    INSTANCE_CONNECTION_NAME: Optional[str] = None 

    # Fallback for local development (standard TCP connection string)
    DATABASE_URL: Optional[str] = None

    # --- INTEROPERABILITY BACKEND SELECTION ---
    # Controls which concrete implementation is used for the FHIR Store and LLM.
    # Supported values for FHIR_BACKEND: "gcp", "noop"
    # Supported values for LLM_BACKEND: "gemini", "noop"
    # This makes the application vendor-neutral and deployable outside GCP.
    FHIR_BACKEND: str = "gcp"
    LLM_BACKEND: str = "gemini"

    # --- GOOGLE CLOUD PLATFORM (only used when FHIR_BACKEND=gcp or LLM_BACKEND=gemini) ---
    GCP_PROJECT_ID: Optional[str] = None
    GCP_LOCATION: Optional[str] = None
    GCP_DATASET_ID: Optional[str] = None
    GCP_FHIR_STORE_ID: Optional[str] = None
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    LLM_MODEL_NAME: str = "gemini-2.5-pro"
    
    # --- SECURITY ---
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200 # 30 days
    
    DEBUG: bool = False
    BACKEND_CORS_ORIGINS: str = ""
    RATE_LIMIT_LOGIN: str = "10/minute"
    RATE_LIMIT_PATIENT_SEARCH: str = "30/minute"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True
    )

settings = Settings()  # type: ignore[call-arg]