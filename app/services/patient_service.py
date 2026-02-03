import logging
import json
from typing import Optional, List
from sqlalchemy.orm import Session
from app.db.models import Patient
from app.schemas.patient import PatientFullRecord

# Setup Logger
logger = logging.getLogger(__name__)

def get_patient_by_nfc(db: Session, nfc_uid: str) -> Optional[Patient]:
    """
    Retrieves a patient record looking up by the NFC Chip UID.
    """
    return db.query(Patient).filter(Patient.nfc_uid == nfc_uid).first()

def search_patients_by_query(db: Session, query_str: str, limit: int = 10) -> List[Patient]:
    """
    Searches for patients by partial match on ID, First Name, or Last Name.
    Case-insensitive search (ILIKE).
    
    Args:
        query_str (str): The search term (e.g., "Sofia" or "84321").
        limit (int): Max number of results to return.
    """
    return db.query(Patient).filter(
        (Patient.id.ilike(f"%{query_str}%")) |
        (Patient.first_name.ilike(f"%{query_str}%")) |
        (Patient.last_name.ilike(f"%{query_str}%"))
    ).limit(limit).all()

def create_or_update_patient(db: Session, patient_in: PatientFullRecord) -> Patient:
    """
    Persists patient data into the local PostgreSQL database.
    
    Strategy:
    - Check if patient exists by ID.
    - If exists -> Update fields AND check if NFC UID changed (Bracelet replacement).
    - If new -> Create record with provided NFC UID.
    
    Args:
        db (Session): Database session.
        patient_in (PatientFullRecord): Pydantic model with patient data.
        
    Returns:
        Patient: The SQLAlchemy model instance (saved).
    """
    
    # 1. Check for existence by internal Patient ID
    existing_patient = db.query(Patient).filter(Patient.id == patient_in.patientId).first()
    
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
        
        # --- NFC Replacement Logic (Critical for lost bracelets) ---
        # If the payload has an NFC UID, and it's different from the stored one, update it.
        if patient_in.nfc_uid and patient_in.nfc_uid != existing_patient.nfc_uid:
            logger.warning(
                f"NFC Replacement detected for patient {patient_in.patientId}. "
                f"Old: {existing_patient.nfc_uid} -> New: {patient_in.nfc_uid}"
            )
            existing_patient.nfc_uid = patient_in.nfc_uid
        
        # Update the raw JSON blob to keep history complete
        existing_patient.full_record_json = full_record_dump
        
        db.commit()
        db.refresh(existing_patient)
        return existing_patient

    else:
        logger.info(f"Creating new patient record: {patient_in.patientId}")
        
        # Use provided NFC UID or generate a placeholder if missing (legacy support)
        nfc_to_save = patient_in.nfc_uid if patient_in.nfc_uid else f"PENDING-{patient_in.patientId}"

        db_patient = Patient(
            id=patient_in.patientId,
            nfc_uid=nfc_to_save, 
            first_name=patient_in.patientInfo.firstName,
            last_name=patient_in.patientInfo.lastName,
            birth_date=patient_in.patientInfo.dob,
            blood_type=patient_in.patientInfo.bloodType,
            
            # Vital Signs
            weight=patient_in.patientInfo.weight,
            height=patient_in.patientInfo.height,
            
            # Full Data Blob
            full_record_json=full_record_dump,
            is_synced_with_cloud=False
        )
        
        db.add(db_patient)
        db.commit()
        db.refresh(db_patient)
        return db_patient