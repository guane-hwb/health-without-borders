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
    get_patient_by_device_uid, 
    search_patients_advanced
)
from app.services.hl7_service import convert_to_hl7
from app.services.gcp_service import send_to_google_healthcare

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scan/{device_uid}", response_model=PatientFullRecord, status_code=status.HTTP_200_OK)
def get_patient_by_device_uid_scan(
    device_uid: str, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get patient details by scanning a hardware identifier (NFC, Barcode, etc.).
    
    Used in the 'Scan Device' view of the mobile app.
    If the device UID is registered, returns the full medical record.
    If not, returns 404 Not Found.

    Security: Only 'org_admin', 'doctor' and 'nurse' can view clinical records.
    """

    if current_user.role not in ["doctor", "nurse", "org_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only medical staff can view patient records."
        )
    
    logger.info(f"User {current_user.email} (Org: {current_user.organization_id}) scanning Device: {device_uid}")
    
    # Delegate database lookup to the service layer
    patient_db = get_patient_by_device_uid(db, device_uid, current_user.organization_id)
    
    if not patient_db:
        logger.warning(f"Device UID {device_uid} not found for Org {current_user.organization_id}.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found. This tag is not registered in your organization."
        )
    
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
    1. Persist data in PostgreSQL.
    2. Convert to HL7v2 standard format.
    3. Push to Google Cloud Healthcare API.
    
    Security: Only users with the 'doctor' role can write/update medical records.
    Returns:
        PatientSyncResponse: Standardized JSON response with synchronization status.
    """
    if current_user.role != "doctor":
        logger.warning(f"Unauthorized write attempt on patient data by {current_user.email} (Role: {current_user.role})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only doctors can create or update patient records."
        )
    
    try:
        # 1. Save DB
        logger.info(f"Doctor {current_user.email} is syncing patient {patient_data.patientId}")
        saved_patient = create_or_update_patient(db, patient_data, current_user.organization_id)
        
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
    first_name: str = Query(..., min_length=2, description="Patient's first name"),
    last_name: str = Query(..., min_length=2, description="Patient's last name"),
    birth_date: date = Query(..., description="Patient's date of birth (YYYY-MM-DD)"),
    guardian_name: Optional[str] = Query(None, min_length=3, description="Guardian's full or partial name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Strict search for patients.
    First name, last name, and date of birth are MANDATORY
    Security: Only 'org_admin', 'doctor' and 'nurse' can view clinical records.
    """
    if current_user.role not in ["doctor", "nurse", "org_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only medical staff can view patient records."
        )
    
    logger.info(f"User {current_user.email} searching. Params -> First: {first_name}, Last: {last_name}, DOB: {birth_date}, Guardian: {guardian_name}")
    
    results = search_patients_advanced(
        db=db, 
        org_id=current_user.organization_id,
        first_name=first_name, 
        last_name=last_name, 
        birth_date=birth_date, 
        guardian_name=guardian_name,
        limit=10
    )
    
    if not results:
        return []
        
    return [p.full_record_json for p in results]