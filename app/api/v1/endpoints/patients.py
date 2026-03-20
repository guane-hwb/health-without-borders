import logging
from typing import List, Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.db.models import User, UserRole
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
from app.core.config import settings
from app.core.rate_limit import limiter

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
    Retrieve a patient's full medical record by scanning their NFC tag or barcode.

    **Guardian 2FA for minors:**
    If the patient is under 18 years old, the `guardian_device_uid` query parameter
    becomes mandatory. The scanned guardian tag must match the one registered in the
    patient's record. Access is denied if they do not match.

    **Parameters:**
    - `device_uid` (path): The hardware identifier scanned from the patient's bracelet.
    - `guardian_device_uid` (query, conditional): Required only if patient is a minor.

    **Allowed roles:** `doctor`, `nurse`.

    **Responses:**
    - `200`: Full patient record returned.
    - `403`: Caller is not `doctor` or `nurse`.
    - `403`: Patient is a minor and `guardian_device_uid` was not provided.
    - `403`: Patient is a minor and guardian tag does not match.
    - `404`: No patient registered with that device UID in this organization.
    """

    if current_user.role not in {UserRole.doctor, UserRole.nurse, UserRole.org_admin}:
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
    Synchronize a patient record from the mobile app to the cloud.

    **Process flow:**
    1. If any visit in `medicalHistory` is missing a diagnosis, the clinical evaluation
    text is automatically analyzed by a medical LLM (Gemini) to suggest one.
    2. The record is persisted or updated in PostgreSQL.
    3. The record is converted to HL7v2 format.
    4. The HL7v2 message is transmitted to the Google Cloud Healthcare API.

    **Nurse restriction:** A `nurse` may call this endpoint to append vaccination records,
    but cannot add new entries to `medicalHistory`. Attempts to do so will return `403`.

    **Allowed roles:** `doctor`, `nurse`.

    **Responses:**
    - `201`: Sync successful. Returns internal ID and GCP transmission status.
    - `403`: Caller is not `doctor` or `nurse`.
    - `500`: Internal error during processing (DB, HL7 conversion, or GCP transmission).
    """
    if current_user.role not in {UserRole.doctor, UserRole.nurse}:
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
@limiter.limit(settings.RATE_LIMIT_PATIENT_SEARCH)
def search_patients(
    request: Request,
    first_name: str = Query(..., min_length=2, description="Patient's first name"),
    last_name: str = Query(..., min_length=2, description="Patient's last name"),
    birth_date: date = Query(..., description="Patient's date of birth (YYYY-MM-DD)"),
    guardian_name: Optional[str] = Query(None, min_length=3, description="Guardian's full or partial name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Search for patients by demographic data within the caller's organization.

    All three parameters are mandatory to prevent overly broad queries on sensitive data.
    Results are strictly scoped to the caller's organization.

    **Required parameters:**
    - `first_name` (min 2 chars)
    - `last_name` (min 2 chars)
    - `birth_date` (format: YYYY-MM-DD)

    **Optional parameters:**
    - `guardian_name` (min 3 chars): Narrows results when multiple patients share the same name and DOB.

    **Allowed roles:** `doctor`, `nurse`.

    **Responses:**
    - `200`: List of matching patient records (empty list if no matches).
    - `403`: Caller is not `doctor` or `nurse`.
    - `422`: One or more mandatory parameters are missing or malformed.
    """
    if current_user.role not in {UserRole.doctor, UserRole.nurse, UserRole.org_admin}:
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