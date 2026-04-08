import logging
import requests
import google.auth
from google.auth.transport.requests import Request
from app.core.config import settings

logger = logging.getLogger(__name__)

def send_to_google_healthcare(fhir_bundle: dict):
    """
    Sends a FHIR Bundle (JSON) to Google Cloud Healthcare API.
    Supports both Local (JSON file) and Cloud Run (Metadata Server) authentication.
    """
  
    if not settings.GCP_PROJECT_ID or not settings.GCP_FHIR_STORE_ID:
        logger.warning("Missing GCP Project Configuration. Skipping cloud upload.")
        return {"status": "skipped", "reason": "No GCP configuration"}

    logger.info(f"Sending FHIR RDA to GCP Store: {settings.GCP_FHIR_STORE_ID}...")

    try:
        # HYBRID AUTHENTICATION
        scopes = ['https://www.googleapis.com/auth/cloud-platform']
        creds, project = google.auth.default(scopes=scopes)
        creds.refresh(Request())

        location = settings.GCP_LOCATION or "us-central1"
        
        # Build FHIR Store URL for executing a Bundle transaction
        base_url = (
            f"https://healthcare.googleapis.com/v1/projects/{settings.GCP_PROJECT_ID}/"
            f"locations/{location}/datasets/{settings.GCP_DATASET_ID}/"
            f"fhirStores/{settings.GCP_FHIR_STORE_ID}/fhir/Bundle"
        )
        
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/fhir+json; charset=utf-8"
        }

        logger.debug(f"Posting FHIR Bundle to: {base_url}")
        
        # In FHIR, to process a Bundle, you post it directly to the root of the FHIR store
        response = requests.post(base_url, headers=headers, json=fhir_bundle)
        response.raise_for_status()

        logger.info("Successfully ingested FHIR RDA message to Google Cloud.")
        return {
            "status": "success", 
            "google_response": response.json()
        }

    except requests.exceptions.RequestException as e:
        error_msg = f"GCP API Error: {str(e)}"
        if e.response is not None:
             error_msg += f" | Body: {e.response.text}"
        logger.error(error_msg)
        return {"status": "error", "error": error_msg}
        
    except Exception as e:
        logger.critical(f"Unexpected error in GCP service: {str(e)}")
        return {"status": "error", "error": str(e)}