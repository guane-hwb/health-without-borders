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
    # 1. VALIDACIÓN AJUSTADA
    # Eliminamos el chequeo de GOOGLE_APPLICATION_CREDENTIALS.
    # Solo validamos que tengamos el ID del proyecto y la configuración del Healthcare API.
    if not settings.GCP_PROJECT_ID or not settings.GCP_HL7_STORE_ID:
        logger.warning("Missing GCP Project Configuration. Skipping cloud upload.")
        return {"status": "skipped", "reason": "No GCP configuration"}

    logger.info(f"Sending HL7 to GCP Store: {settings.GCP_HL7_STORE_ID}...")

    try:
        # 2. AUTENTICACIÓN HÍBRIDA
        # google.auth.default() busca automáticamente en este orden:
        # A. Variable de entorno GOOGLE_APPLICATION_CREDENTIALS (local)
        # B. Metadata Server de Cloud Run (Nube - Automático)
        scopes = ['https://www.googleapis.com/auth/cloud-platform']
        creds, project = google.auth.default(scopes=scopes)

        # Forzamos la obtención del token actual (necesario para el header Authorization)
        creds.refresh(Request())

        # 3. CONSTRUIR URL
        location = settings.GCP_LOCATION or "us-central1"
        
        base_url = (
            f"https://healthcare.googleapis.com/v1/projects/{settings.GCP_PROJECT_ID}/"
            f"locations/{location}/datasets/{settings.GCP_DATASET_ID}/"
            f"hl7V2Stores/{settings.GCP_HL7_STORE_ID}/messages"
        )
        
        # 4. PREPARAR PAYLOAD
        # La API exige que el mensaje HL7 vaya codificado en Base64
        b64_message = base64.b64encode(hl7_content.encode('utf-8')).decode('utf-8')
        payload = {"message": {"data": b64_message}}
        
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        # 5. ENVIAR REQUEST
        # Usamos el método :ingest que es el estándar para subir mensajes
        ingest_url = f"{base_url}:ingest"
        
        logger.debug(f"Posting to: {ingest_url}")
        
        response = requests.post(ingest_url, headers=headers, json=payload)
        response.raise_for_status() # Lanza error si es 400, 401, 403, 500...

        logger.info("✅ Successfully ingested message to Google Cloud.")
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