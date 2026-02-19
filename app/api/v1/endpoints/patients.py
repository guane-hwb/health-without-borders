# app/api/v1/endpoints/patients.py
import logging
from typing import List, Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import User
from app.api.deps import get_current_user

from app.db.session import get_db
from app.schemas.patient import PatientFullRecord, PatientSyncResponse
from app.services.patient_service import (
    create_or_update_patient, 
    get_patient_by_nfc, 
    search_patients_advanced
)
from app.services.hl7_service import convert_to_hl7
from app.services.gcp_service import send_to_google_healthcare

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/nfc/{nfc_uid}", response_model=PatientFullRecord, status_code=status.HTTP_200_OK)
def get_patient_by_nfc_scan(
    nfc_uid: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get patient details by scanning the NFC bracelet.
    
    Used in the 'Read NFC' view of the mobile app.
    If the bracelet is registered, returns the full medical record.
    If not, returns 404 Not Found.
    """
    logger.info(f"User {current_user.email} is scanning NFC UID: {nfc_uid}")
    
    # Delegate database lookup to the service layer
    patient_db = get_patient_by_nfc(db, nfc_uid)
    
    if not patient_db:
        logger.warning(f"NFC UID {nfc_uid} not found in database.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found. This bracelet is not registered."
        )
    
    # We return the stored JSON directly. 
    # Pydantic will validate it against PatientFullRecord schema automatically.
    return patient_db.full_record_json


@router.post("/sync", response_model=PatientSyncResponse, status_code=status.HTTP_201_CREATED)
def sync_patient(
    patient_data: PatientFullRecord, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Synchronize patient data from mobile app.
    
    Process Flow:
    1. Persist data locally in PostgreSQL.
    2. Convert to HL7v2 standard format.
    3. Push to Google Cloud Healthcare API.
    
    Returns:
        PatientSyncResponse: Standardized JSON response with synchronization status.
    """
    try:
        # 1. Save to Local DB
        logger.info(f"User {current_user.email} is syncing patient {patient_data.patientId}")
        saved_patient = create_or_update_patient(db, patient_data)
        
        # 2. HL7 Conversion
        hl7_message = convert_to_hl7(patient_data)
        logger.debug(f"Generated HL7 message. Size: {len(hl7_message)} bytes")

        # 3. Send to GCP
        gcp_response = send_to_google_healthcare(hl7_message)
        
        # Safe extraction of status (defensive programming)
        gcp_status = gcp_response.get("status", "unknown")
        
        if gcp_status != "success":
            logger.warning(f"GCP Upload issue for ID {saved_patient.id}. Status: {gcp_status}")
        else:
            logger.info(f"GCP Upload success for ID {saved_patient.id}")

        return PatientSyncResponse(
            status="success",
            internal_id=str(saved_patient.id),
            gcp_status=gcp_status,
            message="Patient synced and processed successfully"
        )

    except Exception as e:
        logger.error(f"Critical error syncing patient {patient_data.patientId}: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal Server Error processing patient data."
        )

@router.get("/search", response_model=List[PatientFullRecord], status_code=status.HTTP_200_OK)
def search_patients(
    first_name: Optional[str] = Query(None, min_length=2, description="Patient's first name"),
    last_name: Optional[str] = Query(None, min_length=2, description="Patient's last name"),
    birth_date: Optional[date] = Query(None, description="Patient's date of birth (YYYY-MM-DD)"),
    guardian_name: Optional[str] = Query(None, min_length=3, description="Guardian's full or partial name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Flexible advanced search. Allows missing fields (e.g., unaccompanied minors).
    At least one search parameter must be provided.
    """
    
    # Validación de seguridad: Evitar búsquedas vacías que saturen la BD
    if not any([first_name, last_name, birth_date, guardian_name]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one search parameter (first_name, last_name, birth_date, or guardian_name) must be provided."
        )

    logger.info(f"User {current_user.email} searching. Params -> First: {first_name}, Last: {last_name}, DOB: {birth_date}, Guardian: {guardian_name}")
    
    results = search_patients_advanced(
        db=db, 
        first_name=first_name, 
        last_name=last_name, 
        birth_date=birth_date, 
        guardian_name=guardian_name,
        limit=10
    )
    
    if not results:
        return []
        
    return [p.full_record_json for p in results]