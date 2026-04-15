import asyncio
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.models import User, UserRole
from app.db.session import get_db
from app.schemas.patient import PatientFullRecord, PatientSyncResponse
from app.services.fhir_service import convert_to_fhir_rda
from app.services.gcp_service import send_to_google_healthcare
from app.services.llm.service import get_medical_llm_processor
from app.services.patient_service import (
    create_or_update_patient,
    find_patient_strict,
    get_patient_by_device_uid,
)

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
async def sync_patient(
    patient_data: PatientFullRecord, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Synchronize a patient record from the mobile app to the cloud.

    **Process flow:**
    1. If any NEW visit in `medicalHistory` is missing a diagnosis, the clinical 
       evaluation text is analyzed by a medical LLM (Gemini) to suggest one.
    2. If any `familyHistory` item lacks ICD codes, the LLM resolves them.
    3. The record is persisted or updated in PostgreSQL.
    4. Only NEW FHIR RDA Bundles are generated (delta logic):
       - RDA-Paciente: on first sync or when background data changes.
       - RDA-Consulta: only for visits not previously sent.
    5. Bundles are transmitted to the Google Cloud Healthcare API.
    6. Sync tracking counters are updated in the database.

    **Nurse restriction:** A `nurse` may call this endpoint to append vaccination records,
    but cannot add new entries to `medicalHistory`. Attempts to do so will return `403`.

    **Allowed roles:** `doctor`, `nurse`.
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
        processor = get_medical_llm_processor()
        # --- LLM PROCESSING: Only for visits that don't have diagnoses yet ---
        for visit in patient_data.medicalHistory:
            if not visit.diagnosis:
                eval_data = visit.clinicalEvaluation
                ai_diagnoses = await asyncio.to_thread(
                    processor.extract_diagnoses,
                    history=eval_data.historyOfCurrentIllness,
                    physical=eval_data.generalPhysicalExamination,
                    systems=eval_data.systemsExamination,
                    plan=eval_data.treatmentPlanObservations
                )
                visit.diagnosis = ai_diagnoses

        # --- LLM PROCESSING: Family history ICD coding ---
        if patient_data.backgroundHistory and patient_data.backgroundHistory.familyHistory:
            for fh_item in patient_data.backgroundHistory.familyHistory:
                if fh_item.conditionDescription and not fh_item.conditionCie10Code:
                    coded = await asyncio.to_thread(
                        processor.code_family_history_item,
                        fh_item.conditionDescription
                    )
                    fh_item.conditionCie10Code = coded.get("icd10Code")
                    fh_item.conditionCie11Code = coded.get("icd11Code")
                    if coded.get("description"):
                        fh_item.conditionDescription = coded["description"]

        # 1. Save to DB — returns (patient, previous_visit_count)
        logger.info(
            "Patient sync started actor_id=%s role=%s org_id=%s patient_ref=%s",
            current_user.id,
            current_user.role,
            current_user.organization_id,
            _mask_identifier(patient_data.patientId),
        )
        saved_patient, previous_visit_count = create_or_update_patient(
            db, patient_data, current_user.organization_id, current_user.role
        )
        
        # 2. FHIR RDA Conversion — DELTA: only new bundles
        fhir_bundles = convert_to_fhir_rda(
            patient_data,
            previous_visit_count=previous_visit_count,
            rda_paciente_already_sent=saved_patient.rda_paciente_sent,
        )
        logger.debug(f"Generated {len(fhir_bundles)} FHIR RDA Bundle(s) (delta)")

        # 3. Send each Bundle to GCP
        gcp_status = "success" if not fhir_bundles else "unknown"
        all_success = True
        for i, bundle in enumerate(fhir_bundles):
            gcp_response = await asyncio.to_thread(send_to_google_healthcare, bundle)
            bundle_status = gcp_response.get("status", "unknown")
            
            if bundle_status != "success":
                logger.warning(
                    "Patient sync GCP warning patient_ref=%s bundle=%d status=%s",
                    _mask_identifier(str(saved_patient.id)),
                    i,
                    bundle_status,
                )
                gcp_status = bundle_status
                all_success = False
            else:
                if gcp_status == "unknown":
                    gcp_status = "success"
                logger.info(
                    "Patient sync GCP success patient_ref=%s bundle=%d",
                    _mask_identifier(str(saved_patient.id)), i
                )

        # 4. Update sync tracking ONLY if GCP succeeded
        if all_success and fhir_bundles:
            saved_patient.synced_visit_count = len(patient_data.medicalHistory)
            saved_patient.rda_paciente_sent = True
            db.commit()
            logger.info(
                "Sync tracking updated patient_ref=%s visits=%d",
                _mask_identifier(str(saved_patient.id)),
                saved_patient.synced_visit_count
            )

        return PatientSyncResponse(
            status="success",
            internal_id=str(saved_patient.id),
            gcp_status=gcp_status,
            vida_code=None,
            message="Patient synced and processed successfully"
        )

    except HTTPException:
        raise

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

@router.get("/search", response_model=PatientFullRecord, status_code=status.HTTP_200_OK)
@limiter.limit(settings.RATE_LIMIT_PATIENT_SEARCH)
def search_patient(
    request: Request,
    document_number: str = Query(..., min_length=3, description="Número de documento de identidad del paciente (Res. 866 Elem. 2.2)"),
    birth_date: date = Query(..., description="Fecha de nacimiento del paciente (YYYY-MM-DD)"),
    first_name: str = Query(..., min_length=2, description="Primer nombre del paciente"),
    last_name: str = Query(..., min_length=2, description="Primer o segundo apellido del paciente"),
    guardian_name: Optional[str] = Query(None, min_length=3, description="Nombre completo del acudiente (obligatorio si el paciente tiene guardián registrado)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Strict patient lookup by identity — returns exactly one patient or 404.

    This endpoint enforces strict matching to protect patient data privacy 
    in compliance with Ley 1581 de 2012 (Habeas Data) and Ley 1751 de 2015.
    It will **never** return a list of patients.

    **All four parameters are mandatory:**
    - `document_number`: Exact match against the patient's identity document.
    - `birth_date`: Exact match (YYYY-MM-DD).
    - `first_name`: Exact match (case-insensitive).
    - `last_name`: Exact match against first OR second last name (case-insensitive).

    **Conditional parameter:**
    - `guardian_name`: If the patient has a registered guardian, providing this 
      adds an extra layer of verification. Partial match is allowed.

    **Security:**
    - If the criteria match more than one patient (ambiguous), the endpoint 
      returns 404 — it will not expose either record.
    - Results are strictly scoped to the caller's organization (multi-tenant).

    **Allowed roles:** `doctor`, `nurse`, `org_admin`.

    **Responses:**
    - `200`: Patient record found and returned.
    - `403`: Caller is not authorized.
    - `404`: No patient found matching the provided criteria.
    - `422`: Missing or malformed mandatory parameters.
    """
    if current_user.role not in {UserRole.doctor, UserRole.nurse, UserRole.org_admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only medical staff can view patient records."
        )

    logger.info(
        "Patient strict lookup actor_id=%s role=%s org_id=%s doc_ref=%s",
        current_user.id,
        current_user.role,
        current_user.organization_id,
        _mask_identifier(document_number),
    )

    patient = find_patient_strict(
        db=db,
        org_id=current_user.organization_id,
        document_number=document_number,
        birth_date=birth_date,
        first_name=first_name,
        last_name=last_name,
        guardian_name=guardian_name,
    )

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No patient found matching the provided criteria."
        )

    return patient.full_record_json