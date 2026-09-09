import os
import re
from typing import Optional

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

# Highest key version representable in the one-byte NFC payload header.
NFC_MAX_KEY_VERSION = 255

_HEX_KEY_RE = re.compile(r"[0-9a-fA-F]{64}")

#: Matches the per-version NFC key variables, e.g. ``NFC_KEY_V1``.
_NFC_KEY_VAR_RE = re.compile(r"^NFC_KEY_V(\d+)$")


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
    LLM_MODEL_NAME: str = "gemini-3-flash-preview"
    
    # --- SECURITY ---
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # 1 hour
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days
    
    # --- NFC ---
    # Version 0 is reserved for this legacy single key. Tags written before key
    # versioning carry no version header and are decrypted with version 0.
    NFC_MASTER_KEY: str = ""  # Hex-encoded 32-byte AES-256 key (key version 0)
    # Version that new writes are encrypted with. Clients pick the key with this
    # version from the keyring and stamp it into the NFC payload header.
    # Defaults to 0 (the legacy NFC_MASTER_KEY) so existing deployments keep
    # working unchanged. A rotated deployment adds NFC_KEY_V<n> secrets and
    # bumps this to the highest live version.
    NFC_CURRENT_KEY_VERSION: int = 0

    # --- REPORTING ---
    # Calendar dates and month boundaries in aggregated statistics are resolved
    # in this zone. Reporting in UTC would push the last five hours of every
    # Colombian month into the next one.
    STATS_TIMEZONE: str = "America/Bogota"
    # Slim container images do not always ship the IANA tz database. When the
    # zone above cannot be loaded, this fixed offset is used instead. Colombia
    # has observed no daylight saving since 1993, so -5 is exact year-round.
    STATS_TIMEZONE_FALLBACK_OFFSET_HOURS: int = -5

    DEBUG: bool = False
    BACKEND_CORS_ORIGINS: str = ""
    RATE_LIMIT_LOGIN: str = "10/minute"
    RATE_LIMIT_PATIENT_SEARCH: str = "30/minute"
    # Number of trusted reverse proxies that append to X-Forwarded-For.
    # The client IP is read from the entry these proxies added, never from
    # the client-controlled leftmost value. Set to match the deployment
    # (1 = single trusted front proxy, e.g. Cloud Run / a load balancer).
    TRUSTED_PROXY_HOPS: int = 1

    # --- REDIS (for rate limiting and token revocation) ---
    # Optional: falls back to in-memory storage when not set (local dev)
    REDIS_URL: Optional[str] = None

    def nfc_keyring(self) -> dict[int, str]:
        """
        Build the ``{version: hex_key}`` map of every live NFC key.

        Sources, merged in this order (later wins on a version clash):
          - ``NFC_MASTER_KEY``, when set, is registered as version 0 (the
            legacy key; tags written before versioning decrypt with it).
          - Every ``NFC_KEY_V<n>`` entry in the ``.env`` file, if present.
          - Every ``NFC_KEY_V<n>`` environment variable. These are meant to be
            mounted one secret per key from a secret manager, so rotation is
            "add a secret and bump NFC_CURRENT_KEY_VERSION" with no code or
            JSON edits.

        The ``.env`` file is read explicitly because these names are dynamic:
        pydantic-settings only loads it into declared fields, and ``NFC_KEY_V1``
        is not one, so a rotated key in a developer's ``.env`` would otherwise
        be silently ignored while the same name works in production. The
        process environment is applied last so a real environment variable
        still wins, matching how every other setting behaves.

        Blank values are skipped. Returns an empty dict when nothing is set.
        """
        ring: dict[int, str] = {}
        if self.NFC_MASTER_KEY.strip():
            ring[0] = self.NFC_MASTER_KEY.strip()

        for source in (self._dotenv_values(), os.environ):
            for name, value in source.items():
                match = _NFC_KEY_VAR_RE.match(name)
                if match and value and value.strip():
                    ring[int(match.group(1))] = value.strip()
        return ring

    @staticmethod
    def _dotenv_values() -> dict[str, str | None]:
        """Read the ``.env`` file, or an empty mapping when there is none."""
        env_file = Settings.model_config.get("env_file")
        if not env_file or not os.path.exists(env_file):
            return {}
        try:
            return dotenv_values(
                env_file,
                encoding=Settings.model_config.get("env_file_encoding", "utf-8"),
            )
        except OSError:
            # An unreadable .env must not stop the app: the process
            # environment is the authoritative source in deployments.
            return {}

    def nfc_keyring_errors(self) -> list[str]:
        """
        Describe everything wrong with the configured NFC keyring.

        A malformed key is not a local problem: it is served to every device,
        and a client that cannot parse it loses NFC entirely. Checking at
        startup turns a fleet-wide outage discovered when someone taps a
        wristband into a deployment that refuses to boot.

        Returns an empty list when the configuration is usable. A deployment
        with no NFC key at all is valid (NFC is simply unavailable).
        """
        errors: list[str] = []
        ring = self.nfc_keyring()
        if not ring:
            return errors

        for version, key in sorted(ring.items()):
            source = (
                "NFC_MASTER_KEY"
                if version == 0 and key == self.NFC_MASTER_KEY.strip()
                else f"NFC_KEY_V{version}"
            )
            if not 0 <= version <= NFC_MAX_KEY_VERSION:
                errors.append(
                    f"{source}: version must be between 0 and "
                    f"{NFC_MAX_KEY_VERSION} to fit the NFC payload header."
                )
            if not _HEX_KEY_RE.fullmatch(key):
                # Never include the value itself in the message.
                errors.append(
                    f"{source}: must be exactly 64 hexadecimal characters "
                    "(a 32-byte AES-256 key)."
                )

        if self.NFC_CURRENT_KEY_VERSION not in ring:
            errors.append(
                f"NFC_CURRENT_KEY_VERSION={self.NFC_CURRENT_KEY_VERSION} has no "
                f"matching key. Versions configured: {sorted(ring)}."
            )
        return errors

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True
    )

settings = Settings()  # type: ignore[call-arg]