"""
Google Cloud Healthcare API implementation of FHIRStoreBackend.

This is the ONLY file in the project that imports google-auth. All other
code depends on the abstract FHIRStoreBackend Protocol.
"""

import logging
from typing import Optional

import google.auth
import requests
from google.auth.transport.requests import Request

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

        logger.info(f"Sending FHIR Bundle to GCP Store: {self.fhir_store_id}")

        try:
            creds, _ = google.auth.default(scopes=self.SCOPES)
            creds.refresh(Request())

            url = self._build_url()
            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/fhir+json; charset=utf-8",
            }

            logger.debug(f"POST {url}")
            response = requests.post(url, headers=headers, json=bundle)
            response.raise_for_status()

            logger.info("FHIR Bundle successfully ingested by GCP Healthcare API.")
            return {"status": "success", "response": response.json()}

        except requests.exceptions.RequestException as e:
            error_msg = f"GCP Healthcare API error: {str(e)}"
            if e.response is not None:
                error_msg += f" | Body: {e.response.text}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        except Exception as e:
            logger.critical(f"Unexpected error in GCP Healthcare backend: {str(e)}")
            return {"status": "error", "error": str(e)}