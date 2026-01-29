import logging
import json
from sqlalchemy.orm import Session
from app.db.models import Patient
from app.schemas.patient import PatientFullRecord

# Setup Logger
logger = logging.getLogger(__name__)

def create_or_update_patient(db: Session, patient_in: PatientFullRecord) -> Patient:
    """
    Persists patient data into the local PostgreSQL database.
    
    Strategy:
    - Check if patient exists by ID.
    - If exists -> Update fields.
    - If new -> Create record.
    
    Args:
        db (Session): Database session.
        patient_in (PatientFullRecord): Pydantic model with patient data.
        
    Returns:
        Patient: The SQLAlchemy model instance (saved).
    """
    
    # 1. Check for existence
    existing_patient = db.query(Patient).filter(Patient.id == patient_in.patientId).first()
    
    # Serialize the full JSON once to ensure consistency
    full_record_dump = patient_in.model_dump(mode='json')

    if existing_patient:
        logger.info(f"Updating existing patient: {patient_in.patientId}")
        
        # Demographic Updates
        existing_patient.first_name = patient_in.patientInfo.firstName
        existing_patient.last_name = patient_in.patientInfo.lastName
        
        # Physical Updates (Important for malnutrition tracking)
        existing_patient.weight = patient_in.patientInfo.weight
        existing_patient.height = patient_in.patientInfo.height
        
        # Update the raw JSON blob to keep history complete
        existing_patient.full_record_json = full_record_dump
        
        db.commit()
        db.refresh(existing_patient)
        return existing_patient

    else:
        logger.info(f"Creating new patient record: {patient_in.patientId}")
        
        # TODO: Map real NFC UID here once Frontend provides it in the JSON payload.
        # Currently utilizing a placeholder or deriving from ID if needed.
        nfc_placeholder = getattr(patient_in, "nfc_uid", f"NFC-PENDING-{patient_in.patientId}")

        db_patient = Patient(
            id=patient_in.patientId,
            nfc_uid=nfc_placeholder, 
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