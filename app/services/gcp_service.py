import base64
import logging
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from app.core.config import settings

logger = logging.getLogger(__name__)

def send_to_google_healthcare(hl7_content: str):
    """
    Sends an HL7v2 message string to Google Cloud Healthcare API.
    """
    # Validation using Pydantic Settings
    if not settings.GCP_PROJECT_ID or not settings.GOOGLE_APPLICATION_CREDENTIALS:
        logger.warning("Missing GCP Credentials. Skipping cloud upload.")
        return {"status": "skipped", "reason": "No credentials"}

    logger.info(f"Sending HL7 to GCP Store: {settings.GCP_HL7_STORE_ID}...")

    try:
        # 1. Authentication
        creds = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_APPLICATION_CREDENTIALS,
            scopes=['https://www.googleapis.com/auth/cloud-platform']
        )
        creds.refresh(Request())

        # 2. Build URL
        base_url = (
            f"https://healthcare.googleapis.com/v1/projects/{settings.GCP_PROJECT_ID}/"
            f"locations/{settings.GCP_LOCATION}/datasets/{settings.GCP_DATASET_ID}/"
            f"hl7V2Stores/{settings.GCP_HL7_STORE_ID}/messages"
        )
        
        # 3. Payload
        b64_message = base64.b64encode(hl7_content.encode('utf-8')).decode('utf-8')
        payload = {"message": {"data": b64_message}}
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        # 4. Request
        response = requests.post(base_url + ":ingest", headers=headers, json=payload)
        response.raise_for_status() # Raises error for 4xx/5xx codes

        logger.info("Successfully ingested message to Google Cloud.")
        return {
            "status": "success", 
            "google_response": response.json()
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"GCP API Error: {str(e)}")
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.critical(f"Unexpected error in GCP service: {str(e)}")
        raise e