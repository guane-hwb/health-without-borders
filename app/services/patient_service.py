import logging
from datetime import date
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.models import Patient
from app.schemas.patient import PatientFullRecord
from fastapi import HTTPException, status

# Setup Logger
logger = logging.getLogger(__name__)

def get_patient_by_device_uid(db: Session, device_uid: str, org_id: str) -> Optional[Patient]:
    """
    Fetches a patient using a hardware tag, STRICTLY scoped to the user's organization.
    """
    return db.query(Patient).filter(
        Patient.device_uid == device_uid,
        Patient.organization_id == org_id 
    ).first()

def search_patients_advanced(
    db: Session, 
    org_id: str,
    first_name: str, 
    last_name: str, 
    birth_date: str, 
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
            Patient.guardian_name.ilike(f"%{guardian_name}%")
        )
    
    return query.limit(limit).all()

def create_or_update_patient(db: Session, patient_in: PatientFullRecord, org_id: str, current_role: str) -> Patient:
    """
    Persists patient data into the local PostgreSQL database.
    
    Strategy:
    - Check if patient exists by ID.
    - If exists -> Update fields AND check if Device UID changed (Tag/Bracelet replacement).
    - If new -> Create record with provided Device UID.
    
    Args:
        db (Session): Database session.
        patient_in (PatientFullRecord): Pydantic model with patient data.
        org_id (str): Organization ID for multi-tenancy.
        current_role (str): Role of the user performing the operation (for access control).
        
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
    new_record_dump = patient_in.model_dump(mode='json')

    if existing_patient:
        logger.info(f"Updating existing patient: {existing_patient.id}")
        old_record_dump = existing_patient.full_record_json

        # ==========================================================
        # REGLA 1: ENFERMERAS NO PUEDEN AGREGAR HISTORIAL MÉDICO
        # ==========================================================
        if current_role == "nurse":
            old_history_len = len(old_record_dump.get("medicalHistory", []))
            new_history_len = len(new_record_dump.get("medicalHistory", []))
            
            if new_history_len > old_history_len:
                logger.warning(f"Nurse tried to add medical history to patient {existing_patient.id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access Denied: Nurses can only add vaccines, not medical history."
                )

        # ==========================================================
        # REGLA 2: PROTEGER CAMPOS INMUTABLES (Nombre, Fecha Nac, etc)
        # ==========================================================
        # Por seguridad, sobrescribimos los datos del nuevo JSON con 
        # los datos viejos originales para garantizar que no se cambien.
        new_record_dump["patientInfo"]["firstName"] = old_record_dump["patientInfo"]["firstName"]
        new_record_dump["patientInfo"]["lastName"] = old_record_dump["patientInfo"]["lastName"]
        new_record_dump["patientInfo"]["dob"] = old_record_dump["patientInfo"]["dob"]
        new_record_dump["patientInfo"]["gender"] = old_record_dump["patientInfo"]["gender"]
        new_record_dump["patientInfo"]["bloodType"] = old_record_dump["patientInfo"]["bloodType"]

        # ==========================================================
        # REGLA 3: ACTUALIZAR SOLO CAMPOS PERMITIDOS
        # ==========================================================
        existing_patient.guardian_name = patient_in.guardianInfo.name
        existing_patient.guardian_phone = patient_in.guardianInfo.phone
        
        # Guardamos el JSON (que ahora tiene las vacunas nuevas, el nuevo guardian y dirección,
        # pero conserva intacto el nombre y fecha de nacimiento originales).
        existing_patient.full_record_json = new_record_dump
        
        # ==========================================================
        # ESCENARIO 2: REPOSICIÓN DE MANILLA (Actualizar device_uid)
        # ==========================================================
        if patient_in.device_uid and patient_in.device_uid != existing_patient.device_uid:
            logger.info(f"Device Tag updated for patient {existing_patient.id}")
            existing_patient.device_uid = patient_in.device_uid
        
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
            guardian_name=patient_in.guardianInfo.name,
            guardian_phone=patient_in.guardianInfo.phone,
            full_record_json=new_record_dump
        )
        
        db.add(db_patient)
        db.commit()
        db.refresh(db_patient)
        return db_patient