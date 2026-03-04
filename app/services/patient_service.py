import logging
from datetime import date
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.models import Patient
from app.schemas.patient import PatientFullRecord

# Setup Logger
logger = logging.getLogger(__name__)

def get_patient_by_device_uid(db: Session, device_uid: str, org_id: int) -> Optional[Patient]:
    """
    Fetches a patient using a hardware tag, STRICTLY scoped to the user's organization.
    """
    return db.query(Patient).filter(
        Patient.device_uid == device_uid,
        Patient.organization_id == org_id 
    ).first()

def search_patients_advanced(
    db: Session, 
    org_id: int,
    first_name: Optional[str] = None, 
    last_name: Optional[str] = None, 
    birth_date: Optional[date] = None, 
    guardian_name: Optional[str] = None, 
    limit: int = 10
) -> List[Patient]:
    """
    Dynamic search for patients, strictly scoped to the user's organization.
    """
    query = db.query(Patient).filter(
        Patient.organization_id == org_id,
        Patient.first_name.ilike(f"%{first_name}%"),
        Patient.last_name.ilike(f"%{last_name}%"),
        Patient.birth_date == birth_date
    )
        
    if guardian_name:
        query = query.filter(
            func.json_extract_path_text(Patient.full_record_json, 'guardianInfo', 'name').ilike(f"%{guardian_name}%")
        )
    
    return query.limit(limit).all()

def create_or_update_patient(db: Session, patient_in: PatientFullRecord, org_id: int) -> Patient:
    """
    Persists patient data into the local PostgreSQL database.
    
    Strategy:
    - Check if patient exists by ID.
    - If exists -> Update fields AND check if Device UID changed (Tag/Bracelet replacement).
    - If new -> Create record with provided Device UID.
    
    Args:
        db (Session): Database session.
        patient_in (PatientFullRecord): Pydantic model with patient data.
        
    Returns:
        Patient: The SQLAlchemy model instance (saved).
    """
    
    # 1. Check for existence by ID AND Organization (Seguridad Multi-Tenant)
    # Así evitamos que un médico de la ONG 'A' modifique por error a un paciente de la ONG 'B'
    existing_patient = db.query(Patient).filter(
        Patient.id == patient_in.patientId,
        Patient.organization_id == org_id
    ).first()
    
    # Serialize the full JSON once to ensure consistency
    full_record_dump = patient_in.model_dump(mode='json')

    if existing_patient:
        logger.info(f"Updating existing patient: {patient_in.patientId}")
        
        # --- Demographic Updates ---
        existing_patient.first_name = patient_in.patientInfo.firstName
        existing_patient.last_name = patient_in.patientInfo.lastName
        
        # --- Physical Updates ---
        existing_patient.weight = patient_in.patientInfo.weight
        existing_patient.height = patient_in.patientInfo.height
        
        # --- Device Replacement Logic (Critical for lost bracelets/QRs) ---
        # If the payload has a Device UID, and it's different from the stored one, update it.
        if patient_in.device_uid and patient_in.device_uid != existing_patient.device_uid:
            logger.warning(
                f"Device Replacement detected for patient {patient_in.patientId}. "
                f"Old: {existing_patient.device_uid} -> New: {patient_in.device_uid}"
            )
            existing_patient.device_uid = patient_in.device_uid
        
        # Update the raw JSON blob to keep history complete
        existing_patient.full_record_json = full_record_dump
        
        db.commit()
        db.refresh(existing_patient)
        return existing_patient

    else:
        logger.info(f"Creating new patient record: {patient_in.patientId}")
        
        # Use provided Device UID or generate a placeholder if missing (legacy support)
        device_to_save = patient_in.device_uid if patient_in.device_uid else f"PENDING-{patient_in.patientId}"

        db_patient = Patient(
            id=patient_in.patientId,
            organization_id=org_id,
            device_uid=device_to_save, 
            first_name=patient_in.patientInfo.firstName,
            last_name=patient_in.patientInfo.lastName,
            birth_date=patient_in.patientInfo.dob,
            blood_type=patient_in.patientInfo.bloodType,
            weight=patient_in.patientInfo.weight,
            height=patient_in.patientInfo.height,
            full_record_json=full_record_dump,
            is_synced_with_cloud=False
        )
        
        db.add(db_patient)
        db.commit()
        db.refresh(db_patient)
        return db_patient