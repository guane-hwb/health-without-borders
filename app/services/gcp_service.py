from google.auth.transport.requests import Request
from google.oauth2 import service_account
import requests
import json
import os
import base64

# Configuración desde variables de entorno (Las definiremos en el .env)
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
DATASET_ID = os.getenv("GCP_DATASET_ID", "salud_migrantes_dataset")
HL7_STORE_ID = os.getenv("GCP_HL7_STORE_ID", "unicef_store")
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

def send_to_google_healthcare(hl7_content: str):
    """
    Envía el mensaje HL7v2 string a Google Cloud Healthcare API.
    """
    if not PROJECT_ID or not CREDENTIALS_PATH:
        print("⚠️  Faltan credenciales de GCP. Saltando envío a nube.")
        return {"status": "skipped", "reason": "No credentials"}

    print(f"☁️  Enviando a Google Cloud: {PROJECT_ID} / {HL7_STORE_ID}...")

    # 1. Autenticación Manual (Cargando el JSON de la cuenta de servicio)
    # Nota: Usamos requests directo a la API REST para máxima compatibilidad
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH,
        scopes=['https://www.googleapis.com/auth/cloud-platform']
    )
    creds.refresh(Request()) # Obtener token fresco

    # 2. Construir la URL de la API de Healthcare
    base_url = f"https://healthcare.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/datasets/{DATASET_ID}/hl7V2Stores/{HL7_STORE_ID}/messages"
    
    # 3. Preparar el Payload
    # Google pide el mensaje en base64 dentro de un JSON
    b64_message = base64.b64encode(hl7_content.encode('utf-8')).decode('utf-8')
    
    payload = {
        "message": {
            "data": b64_message
        }
    }

    # 4. Enviar (POST)
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    response = requests.post(base_url + ":ingest", headers=headers, json=payload)

    # 5. Manejar respuesta
    if response.status_code == 200:
        print("✅ ¡Éxito! Mensaje recibido por Google Cloud.")
        return response.json()
    else:
        print(f"❌ Error GCP: {response.text}")
        raise Exception(f"Google Cloud Error: {response.status_code} - {response.text}")