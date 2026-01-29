# app/api/v1/endpoints/patients.py
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.patient import PatientFullRecord, PatientSyncResponse
from app.services.patient_service import create_or_update_patient
from app.services.hl7_service import convert_to_hl7
from app.services.gcp_service import send_to_google_healthcare

# Setup logger
logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/sync", response_model=PatientSyncResponse, status_code=status.HTTP_201_CREATED)
def sync_patient(patient_data: PatientFullRecord, db: Session = Depends(get_db)):
    """
    Synchronize patient data from mobile app.
    
    1. Persist data locally in PostgreSQL.
    2. Convert to HL7v2 format.
    3. Push to Google Cloud Healthcare API.
    """
    try:
        # 1. Save to Local DB
        logger.info(f"Receiving sync request for patient: {patient_data.patientId}")
        saved_patient = create_or_update_patient(db, patient_data)
        
        # 2. HL7 Conversion
        hl7_message = convert_to_hl7(patient_data)
        logger.debug(f"Generated HL7 message size: {len(hl7_message)} bytes")

        # 3. Send to GCP
        gcp_response = send_to_google_healthcare(hl7_message)
        
        gcp_status = gcp_response.get("status", "unknown")
        if gcp_status != "success":
            logger.warning(f"GCP Upload failed or skipped for ID {saved_patient.id}")

        return {
            "status": "success", 
            "internal_id": saved_patient.id,
            "gcp_status": gcp_status,
            "message": "Patient synced and processed successfully"
        }

    except Exception as e:
        logger.error(f"Critical error syncing patient {patient_data.patientId}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal Server Error processing patient data."
        )