import logging
from datetime import date
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Patient
from app.schemas.patient import PatientFullRecord

# Setup Logger
logger = logging.getLogger(__name__)


def get_patient_by_device_uid(db: Session, device_uid: str) -> Optional[Patient]:
    """
    Fetches a patient using a hardware tag.
    Patients are global — any authenticated professional can access any patient.
    """
    return db.query(Patient).filter(
        Patient.device_uid == device_uid,
    ).first()


def find_patient_strict(
    db: Session,
    document_number: str,
    birth_date: date,
    first_name: str,
    last_name: str,
    guardian_name: Optional[str] = None,
) -> Optional[Patient]:
    """
    Strict patient lookup — returns exactly one patient or None.

    All four mandatory parameters must match for a result to be returned.
    This prevents accidental exposure of patient data and complies with 
    Ley 1581 de 2012 (Habeas Data) and Resolución 1888/2025 privacy requirements.

    Matching rules:
      - document_number: exact match (case-insensitive)
      - birth_date:      exact match
      - first_name:      exact match (case-insensitive, trimmed)
      - last_name:       exact match against first OR second last name (case-insensitive)
      - guardian_name:    if provided, must match (case-insensitive, partial)

    Returns None if zero or more than one patient matches (ambiguous = denied).
    """
    query = db.query(Patient).filter(
        func.lower(Patient.document_number) == document_number.strip().lower(),
        Patient.birth_date == birth_date,
        func.lower(Patient.first_name) == first_name.strip().lower(),
    )

    # Last name must match either first or second last name
    last_name_lower = last_name.strip().lower()
    query = query.filter(
        (func.lower(Patient.last_name) == last_name_lower)
        | (func.lower(Patient.second_last_name) == last_name_lower)
    )

    # Guardian verification — if provided, it must match either guardian
    if guardian_name:
        guardian_lower = guardian_name.strip().lower()
        query = query.filter(
            (func.lower(Patient.guardian_name).contains(guardian_lower))
            | (func.lower(Patient.guardian2_name).contains(guardian_lower))
        )

    results = query.limit(2).all()

    if len(results) == 1:
        return results[0]

    if len(results) > 1:
        logger.warning(
            "Ambiguous patient lookup doc=%s — %d matches, access denied",
            document_number[:3] + "***",
            len(results),
        )

    return None


def create_or_update_patient(
    db: Session, patient_in: PatientFullRecord, org_id: str, current_role: str
) -> tuple["Patient", int]:
    """
    Persists patient data into the local PostgreSQL database.
    
    Returns:
        Tuple of (Patient instance, previous_visit_count).
        previous_visit_count is used by the caller to determine which 
        medicalHistory entries are new and need FHIR bundles generated.
    """

    # 1. Check for existence by ID (patients are global)
    existing_patient = db.query(Patient).filter(
        Patient.id == patient_in.patientId,
    ).first()

    # Serialize the full JSON once to ensure consistency
    new_record_dump = patient_in.model_dump(mode="json")

    if existing_patient:
        logger.info(f"Updating existing patient: {existing_patient.id}")
        old_record_dump = existing_patient.full_record_json
        previous_visit_count = existing_patient.synced_visit_count

        # RULE 1: NURSES CANNOT ADD MEDICAL HISTORY
        if current_role == "nurse":
            old_history_len = len(old_record_dump.get("medicalHistory", []))
            new_history_len = len(new_record_dump.get("medicalHistory", []))

            if new_history_len > old_history_len:
                logger.warning(
                    f"Nurse tried to add medical history to patient {existing_patient.id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access Denied: Nurses can only add vaccines, not medical history.",
                )

        # RULE 2: PROTECT IMMUTABLE FIELDS (Name, DOB, document, sex)
        new_record_dump["patientInfo"]["firstName"] = old_record_dump["patientInfo"]["firstName"]
        new_record_dump["patientInfo"]["firstLastName"] = old_record_dump["patientInfo"]["firstLastName"]
        new_record_dump["patientInfo"]["secondLastName"] = old_record_dump["patientInfo"].get("secondLastName")
        new_record_dump["patientInfo"]["secondName"] = old_record_dump["patientInfo"].get("secondName")
        new_record_dump["patientInfo"]["dob"] = old_record_dump["patientInfo"]["dob"]
        new_record_dump["patientInfo"]["biologicalSex"] = old_record_dump["patientInfo"]["biologicalSex"]
        new_record_dump["patientInfo"]["bloodType"] = old_record_dump["patientInfo"].get("bloodType")
        new_record_dump["patientInfo"]["identification"] = old_record_dump["patientInfo"]["identification"]

        # RULE 3: UPDATE ONLY ALLOWED FIELDS (guardian, address, vaccines)
        existing_patient.guardian_name = patient_in.guardianInfo.name
        existing_patient.guardian_phone = patient_in.guardianInfo.phone

        # Persist guardian2 name to relational column for search if present
        if patient_in.guardian2Info and patient_in.guardian2Info.name:
            existing_patient.guardian2_name = patient_in.guardian2Info.name

        # Save the JSON (new vaccines/guardian/address, but original immutable fields)
        existing_patient.full_record_json = new_record_dump

        # BRACELET REPLACEMENT (Update device_uid)
        if patient_in.device_uid and patient_in.device_uid != existing_patient.device_uid:
            logger.info(f"Device Tag updated for patient {existing_patient.id}")
            existing_patient.device_uid = patient_in.device_uid

        db.commit()
        db.refresh(existing_patient)
        return existing_patient, previous_visit_count

    else:
        logger.info(f"Creating new patient record: {patient_in.patientId}")
        pi = patient_in.patientInfo

        db_patient = Patient(
            id=patient_in.patientId,
            organization_id=org_id,
            device_uid=patient_in.device_uid,
            document_type=pi.identification.documentType.value,
            document_number=pi.identification.documentNumber,
            first_name=pi.firstName,
            last_name=pi.firstLastName,
            second_last_name=pi.secondLastName,
            birth_date=pi.dob,
            biological_sex=pi.biologicalSex.value,
            blood_type=pi.bloodType,
            nationality_code=pi.nationalityCode,
            guardian_name=patient_in.guardianInfo.name,
            guardian_phone=patient_in.guardianInfo.phone,
            guardian2_name=patient_in.guardian2Info.name if patient_in.guardian2Info else None,
            guardian2_phone=patient_in.guardian2Info.phone if patient_in.guardian2Info else None,
            full_record_json=new_record_dump,
            synced_visit_count=0,
            rda_paciente_sent=False,
        )

        db.add(db_patient)
        db.commit()
        db.refresh(db_patient)
        return db_patient, 0