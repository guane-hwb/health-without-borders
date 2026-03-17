import base64
import logging
import requests
import google.auth
from google.auth.transport.requests import Request
from app.core.config import settings

logger = logging.getLogger(__name__)

def send_to_google_healthcare(hl7_content: str):
    """
    Sends an HL7v2 message string to Google Cloud Healthcare API.
    Supports both Local (JSON file) and Cloud Run (Metadata Server) authentication.
    """
  
    if not settings.GCP_PROJECT_ID or not settings.GCP_HL7_STORE_ID:
        logger.warning("Missing GCP Project Configuration. Skipping cloud upload.")
        return {"status": "skipped", "reason": "No GCP configuration"}

    logger.info(f"Sending HL7 to GCP Store: {settings.GCP_HL7_STORE_ID}...")

    try:
        # HYBRID AUTHENTICATION
        # google.auth.default() automatically searches in this order:
        # A. GOOGLE_APPLICATION_CREDENTIALS environment variable (local)
        # B. Cloud Run Metadata Server (Cloud - Automatic)
        scopes = ['https://www.googleapis.com/auth/cloud-platform']
        creds, project = google.auth.default(scopes=scopes)

        # Force token refresh (necessary for Authorization header)
        creds.refresh(Request())

        # BUILD URL
        location = settings.GCP_LOCATION or "us-central1"
        
        base_url = (
            f"https://healthcare.googleapis.com/v1/projects/{settings.GCP_PROJECT_ID}/"
            f"locations/{location}/datasets/{settings.GCP_DATASET_ID}/"
            f"hl7V2Stores/{settings.GCP_HL7_STORE_ID}/messages"
        )
        
        # PREPARAR PAYLOAD
        # API requires the HL7 message to be Base64 encoded
        b64_message = base64.b64encode(hl7_content.encode('utf-8')).decode('utf-8')
        payload = {"message": {"data": b64_message}}
        
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        ingest_url = f"{base_url}:ingest"
        
        logger.debug(f"Posting to: {ingest_url}")
        
        response = requests.post(ingest_url, headers=headers, json=payload)
        response.raise_for_status()

        logger.info("Successfully ingested message to Google Cloud.")
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