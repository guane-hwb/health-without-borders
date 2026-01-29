from sqlalchemy.orm import Session
from app.db.models import Patient
from app.schemas.patient import PatientFullRecord
import json

def create_or_update_patient(db: Session, patient_in: PatientFullRecord):
    # 1. Verificar si el paciente ya existe (por ID interno o NFC)
    existing_patient = db.query(Patient).filter(Patient.id == patient_in.patientId).first()
    
    if existing_patient:
        # Lógica de Actualización (Update)
        existing_patient.first_name = patient_in.patientInfo.firstName
        existing_patient.last_name = patient_in.patientInfo.lastName
        existing_patient.full_record_json = patient_in.model_dump(mode='json')
        # Aquí se puede agregar lógica para fusionar historiales médicos nuevos
        db.commit()
        db.refresh(existing_patient)
        return existing_patient
    else:
        # Lógica de Creación (Create)
        db_patient = Patient(
            id=patient_in.patientId,
            nfc_uid="NFC-PENDIENTE", # Ojo: JSON actual no trae el NFC UID separado, asumiremos que viene o se genera.
            first_name=patient_in.patientInfo.firstName,
            last_name=patient_in.patientInfo.lastName,
            birth_date=patient_in.patientInfo.dob,
            blood_type=patient_in.patientInfo.bloodType,
            weight=patient_in.patientInfo.weight,
            height=patient_in.patientInfo.height,
            full_record_json=patient_in.model_dump(mode='json'), # Guardamos todo el JSON para no perder nada
            is_synced_with_cloud=False
        )
        db.add(db_patient)
        db.commit()
        db.refresh(db_patient)
        return db_patient