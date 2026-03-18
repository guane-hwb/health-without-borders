import logging
from typing import List, Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import User
from app.api.deps import get_current_user

from app.db.session import get_db
from app.schemas.patient import PatientFullRecord, PatientSyncResponse
from app.services.llm.service import medical_llm_processor
from app.services.patient_service import (
    create_or_update_patient,
    get_patient_by_device_uid, 
    search_patients_advanced
)
from app.services.hl7_service import convert_to_hl7
from app.services.gcp_service import send_to_google_healthcare

logger = logging.getLogger(__name__)

router = APIRouter()

def _mask_identifier(value: Optional[str]) -> str:
    if not value:
        return "unknown"
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"

@router.get("/scan/{device_uid}", response_model=PatientFullRecord, status_code=status.HTTP_200_OK)
def get_patient_by_device_uid_scan(
    device_uid: str, 
    guardian_device_uid: Optional[str] = Query(None, description="Scanned ID from guardian's bracelet"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get patient details by scanning a hardware identifier (NFC, Barcode, etc.).
    Includes 2FA Hardware check for minors using the guardian's NFC tag.
    
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
    
    logger.info(
        "Patient scan request actor_id=%s org_id=%s device_ref=%s",
        current_user.id,
        current_user.organization_id,
        _mask_identifier(device_uid),
    )
    
    # Delegate database lookup to the service layer
    patient_db = get_patient_by_device_uid(db, device_uid, current_user.organization_id)
    
    if not patient_db:
        logger.warning(
            "Patient scan not found org_id=%s device_ref=%s",
            current_user.organization_id,
            _mask_identifier(device_uid),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found. This tag is not registered in your organization."
        )
    
    # Calculate patient's age to determine if guardian authentication is required
    today = date.today()
    dob = patient_db.birth_date
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    # If patient is a minor, require guardian device UID and validate it against the stored guardian info
    if age < 18:
        if not guardian_device_uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guardian bracelet scan required for minors."
            )
        
        # Extract the stored guardian device UID from the patient's full record JSON
        stored_guardian_uid = patient_db.full_record_json.get("guardianInfo", {}).get("device_uid")
        
        if guardian_device_uid != stored_guardian_uid:
            logger.warning(
                "Guardian validation failed actor_id=%s org_id=%s patient_ref=%s",
                current_user.id,
                current_user.organization_id,
                _mask_identifier(patient_db.id),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guardian tag mismatch. Access denied."
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
    if current_user.role not in ["doctor", "nurse"]:
        logger.warning(
            "Unauthorized patient sync actor_id=%s role=%s org_id=%s",
            current_user.id,
            current_user.role,
            current_user.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only doctors and nurses can create or update patient records."
        )
    
    try:
        for visit in patient_data.medicalHistory:
            # If diagnosis is missing, use the LLM to extract it from the clinical evaluation text fields
            if not visit.diagnosis:
                eval_data = visit.clinicalEvaluation
                
                # Requests the medical LLM to analyze the clinical evaluation and extract potential diagnoses
                ai_diagnoses = medical_llm_processor.extract_diagnoses(
                    history=eval_data.historyOfCurrentIllness,
                    physical=eval_data.generalPhysicalExamination,
                    systems=eval_data.systemsExamination,
                    plan=eval_data.treatmentPlanObservations
                )
                
                # Update the visit's diagnosis field with the LLM's output.
                visit.diagnosis = ai_diagnoses

        # 1. Save DB
        logger.info(
            "Patient sync started actor_id=%s role=%s org_id=%s patient_ref=%s",
            current_user.id,
            current_user.role,
            current_user.organization_id,
            _mask_identifier(patient_data.patientId),
        )
        saved_patient = create_or_update_patient(db, patient_data, current_user.organization_id, current_user.role)
        
        # 2. HL7 Conversion
        hl7_message = convert_to_hl7(patient_data)
        logger.debug(f"Generated HL7 message. Size: {len(hl7_message)} bytes")

        # 3. Send to GCP
        gcp_response = send_to_google_healthcare(hl7_message)
        
        gcp_status = gcp_response.get("status", "unknown")
        
        if gcp_status != "success":
            logger.warning(
                "Patient sync GCP warning patient_ref=%s status=%s",
                _mask_identifier(str(saved_patient.id)),
                gcp_status,
            )
        else:
            logger.info("Patient sync GCP success patient_ref=%s", _mask_identifier(str(saved_patient.id)))

        return PatientSyncResponse(
            status="success",
            internal_id=str(saved_patient.id),
            gcp_status=gcp_status,
            message="Patient synced and processed successfully"
        )

    except Exception:
        logger.exception(
            "Critical error during patient sync actor_id=%s org_id=%s",
            current_user.id,
            current_user.organization_id,
        )
        
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
    
    logger.info(
        "Patient search request actor_id=%s role=%s org_id=%s",
        current_user.id,
        current_user.role,
        current_user.organization_id,
    )
    
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