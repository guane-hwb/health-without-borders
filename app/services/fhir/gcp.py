"""
Google Cloud Healthcare API implementation of FHIRStoreBackend.

This is the ONLY file in the project that imports google-auth. All other
code depends on the abstract FHIRStoreBackend Protocol.
"""

import logging
import threading
from typing import Optional

import google.auth
import requests
from google.auth.transport.requests import Request
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import settings
from app.core.phi_sanitizer import sanitize_error_body
from app.services.fhir.base import FHIRBundleSendResult, FHIRStoreBackend

logger = logging.getLogger(__name__)


class GCPHealthcareBackend(FHIRStoreBackend):
    """
    Sends FHIR Bundles to Google Cloud Healthcare API FHIR Store.
    
    Uses Application Default Credentials (ADC):
      - Locally: GOOGLE_APPLICATION_CREDENTIALS env var pointing to a service account key.
      - On Cloud Run: the runtime service account via the metadata server.
    """

    SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
    DEFAULT_LOCATION = "us-central1"

    def __init__(
        self,
        project_id: Optional[str],
        dataset_id: Optional[str],
        fhir_store_id: Optional[str],
        location: Optional[str] = None,
    ) -> None:
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.fhir_store_id = fhir_store_id
        self.location = location or self.DEFAULT_LOCATION
        self._credentials = None
        self._credentials_lock = threading.Lock()
        self._session = self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        """
        One pooled session with bounded retries.

        Only responses that mean "not processed, try later" (429, 503) and
        connection failures are retried, twice, honouring Retry-After: a POST
        that may have been stored is never repeated blindly.
        """
        retry = Retry(
            total=2,
            connect=2,
            read=0,
            status=2,
            status_forcelist=(429, 503),
            allowed_methods=frozenset({"POST"}),
            backoff_factor=0.5,
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def _access_token(self) -> str:
        """Reuse the credentials; refresh only when the token is missing or expired."""
        with self._credentials_lock:
            if self._credentials is None:
                self._credentials, _ = google.auth.default(scopes=self.SCOPES)
            if not self._credentials.valid:
                self._credentials.refresh(Request())
            return self._credentials.token

    def _is_configured(self) -> bool:
        return bool(
            self.project_id and self.dataset_id and self.fhir_store_id
        )

    def _build_url(self) -> str:
        return (
            f"https://healthcare.googleapis.com/v1/projects/{self.project_id}/"
            f"locations/{self.location}/datasets/{self.dataset_id}/"
            f"fhirStores/{self.fhir_store_id}/fhir/Bundle"
        )

    def send_bundle(self, bundle: dict) -> FHIRBundleSendResult:
        if not self._is_configured():
            logger.warning("GCP Healthcare backend is not fully configured. Skipping upload.")
            return {"status": "skipped", "reason": "Missing GCP configuration"}

        logger.info("Sending FHIR Bundle to GCP Store: %s", self.fhir_store_id)

        try:
            url = self._build_url()
            headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/fhir+json; charset=utf-8",
            }

            logger.debug("POST %s", url)
            response = self._session.post(
                url,
                headers=headers,
                json=bundle,
                timeout=(
                    settings.FHIR_CONNECT_TIMEOUT_SECONDS,
                    settings.FHIR_READ_TIMEOUT_SECONDS,
                ),
            )
            response.raise_for_status()

            logger.info("FHIR Bundle successfully ingested by GCP Healthcare API.")
            return {"status": "success", "response": response.json()}

        except requests.exceptions.RequestException as e:
            error_msg = f"GCP Healthcare API error: {type(e).__name__}"
            if e.response is not None:
                error_msg += f" status={e.response.status_code}"
                error_msg += f" | Body: {sanitize_error_body(e.response.text)}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        except Exception as e:
            logger.critical("Unexpected error in GCP Healthcare backend: %s", type(e).__name__)
            return {"status": "error", "error": f"Unexpected: {type(e).__name__}"}
