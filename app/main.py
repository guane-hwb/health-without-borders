from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.patient_service import create_or_update_patient
from app.services.hl7_service import convert_to_hl7
from app.services.gcp_service import send_to_google_healthcare
from app.schemas.patient import PatientFullRecord, PatientSyncResponse
from app.schemas.catalog import CatalogSyncResponse

app = FastAPI(title="Salud Sin Fronteras API")

# 1. Endpoint para Descarga de Catálogos
# Este sigue siendo Mock por ahora hasta que conectemos la consulta a la DB de catálogos
@app.get("/api/v1/catalogs/sync", response_model=CatalogSyncResponse)
def get_catalogs():
    return {
        "diagnoses": [
            {"code": "A09", "description": "Gastroenteritis", "is_common": True},
            {"code": "J00", "description": "Resfriado Común", "is_common": True}
        ],
        "vaccines": [
            {"code": 90707, "name": "Triple Viral", "is_active": True}
        ],
        "version": "v1.0"
    }

# 2. Endpoint para Subida de Pacientes
# Usamos PatientFullRecord (Input) y PatientSyncResponse (Output)
@app.post("/api/v1/patients/sync", response_model=PatientSyncResponse)
def sync_patient(patient_data: PatientFullRecord, db: Session = Depends(get_db)):
    try:
        # 1. Guardar en PostgreSQL Local
        saved_patient = create_or_update_patient(db, patient_data)
        
        # 2. TRADUCCIÓN HL7
        hl7_message = convert_to_hl7(patient_data)
        
        print("----- MENSAJE HL7 GENERADO PARA GOOGLE HEALTHCARE -----")
        print(hl7_message.replace('\r', '\n'))
        print("-------------------------------------------------------")
        
        # 3. ENVIAR A GOOGLE CLOUD
        # Esto intentará enviarlo. Si hay credenciales configuradas,
        # el código imprimirá "Saltando envío" y no fallará.
        gcp_response = send_to_google_healthcare(hl7_message)

        return {
            "status": "success",
            "internal_id": saved_patient.id,
            "gcp_status": gcp_response.get("status", "unknown"),
            "message": "Paciente procesado correctamente"
        }

    except Exception as e:
        print(f"Error crítico: {e}")
        raise HTTPException(status_code=500, detail=f"Error guardando paciente: {str(e)}")