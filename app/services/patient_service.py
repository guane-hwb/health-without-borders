import hashlib
import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.phi_sanitizer import mask_id, safe_patient_ref
from app.db.models import Patient, RetiredDeviceUid
from app.schemas.patient import PatientFullRecord
from app.services.record_merger import merge_patient_records

# Setup Logger
logger = logging.getLogger(__name__)


class DeviceUidConflictError(Exception):
    """
    Raised when a patient sync would bind a ``device_uid`` (NFC tag / bracelet)
    that is already registered to a different patient.

    ``device_uid`` is globally unique, so this covers both a brand-new patient
    reusing an existing tag and a bracelet replacement that points an existing
    patient at a tag owned by someone else. The API layer maps this to a
    ``409 Conflict`` instead of a generic ``500``.
    """


# ---------------------------------------------------------------------------
# Background data hash computation
# ---------------------------------------------------------------------------

def compute_background_hash(record: dict) -> str:
    """
    Compute a SHA-256 hash over the patient fields that are part of the
    RDA-Paciente bundle (everything except medicalHistory and
    vaccinationRecord, which go into RDA-Consulta bundles).

    When this hash changes between syncs, the RDA-Paciente bundle must
    be re-generated and re-sent to the FHIR Store.

    Fields included:
      - patientInfo (demographics, identification)
      - guardianInfo / guardian2Info
      - backgroundHistory (chronic conditions, family history, medications, etc.)
      - allergies

    NOTE: medications are part of backgroundHistory.medications, so they are
    already covered by the "backgroundHistory" field below. (A previous version
    referenced a non-existent top-level "medications" key, which always resolved
    to None and added nothing to the hash.)
    """
    background_fields = {
        "patientInfo": record.get("patientInfo"),
        "guardianInfo": record.get("guardianInfo"),
        "guardian2Info": record.get("guardian2Info"),
        "backgroundHistory": record.get("backgroundHistory"),
        "allergies": record.get("allergies"),
    }
    # Deterministic serialization — sort keys so field order doesn't
    # cause false positives.
    serialized = json.dumps(background_fields, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_patient_by_device_uid(db: Session, device_uid: str) -> Optional[Patient]:
    """
    Fetches a patient using a hardware tag.
    Patients are global — any authenticated professional can access any patient.
    """
    return db.query(Patient).filter(
        Patient.device_uid == device_uid,
    ).first()


def get_retired_device_uid(
    db: Session, device_uid: str
) -> Optional[RetiredDeviceUid]:
    """
    Return the most recent retirement record for a ``device_uid``, or None.

    Used so a scan of a bracelet that was retired (lost/damaged/replaced) can be
    answered distinctly — "this tag was retired and no longer belongs to HWB" —
    instead of as a generic 404 that looks like a blank or unknown chip.
    """
    return (
        db.query(RetiredDeviceUid)
        .filter(RetiredDeviceUid.device_uid == device_uid)
        .order_by(RetiredDeviceUid.retired_at.desc())
        .first()
    )


def _record_guardian_retirements(
    db: Session,
    *,
    old_record: Optional[dict],
    patient_in: PatientFullRecord,
    patient_id: str,
    reason: str,
    actor_user_id: Optional[str],
) -> None:
    """
    Append retirement rows for any guardian card UID that is being replaced or
    removed on this sync.

    Guardian UIDs live inside ``full_record_json`` (``guardianInfo.device_uid``
    and ``guardian2Info.device_uid``), not on a Patient column. We compare the
    persisted (old) UID of each guardian slot against the incoming one; when the
    old UID is non-empty and differs from the new one, the old UID is retired
    with ``device_role='guardian'``.

    Idempotent: after a successful sync the stored UID equals the incoming one,
    so a retried sync retires nothing further. A slot whose guardian was removed
    (incoming UID empty/absent) still retires the old UID.
    """
    old = old_record or {}
    guardian2 = patient_in.guardian2Info
    slots = (
        (
            (old.get("guardianInfo") or {}).get("device_uid"),
            patient_in.guardianInfo.device_uid,
        ),
        (
            (old.get("guardian2Info") or {}).get("device_uid"),
            guardian2.device_uid if guardian2 is not None else None,
        ),
    )
    for old_uid, new_uid in slots:
        old_norm = (old_uid or "").strip()
        new_norm = (new_uid or "").strip()
        if old_norm and old_norm != new_norm:
            db.add(
                RetiredDeviceUid(
                    device_uid=old_norm,
                    patient_id=patient_id,
                    reason=reason,
                    retired_by=actor_user_id,
                    device_role="guardian",
                )
            )
            logger.info(
                "Guardian card retired for patient %s reason=%s",
                safe_patient_ref(patient_id),
                reason,
            )


def _ensure_device_uid_available(
    db: Session, device_uid: str, exclude_patient_id: Optional[str] = None
) -> None:
    """
    Guard the global uniqueness of ``device_uid`` before a write.

    Raises ``DeviceUidConflictError`` if the tag is already bound to a different
    patient. On creation, any existing owner is a conflict. On bracelet
    replacement, the tag's current owner may legitimately be the same patient,
    so ``exclude_patient_id`` is used to skip that self-match.
    """
    owner = get_patient_by_device_uid(db, device_uid)
    if owner is not None and owner.id != exclude_patient_id:
        logger.warning(
            "Device tag already registered to another patient existing_ref=%s device=%s",
            safe_patient_ref(owner.id),
            mask_id(device_uid),
        )
        raise DeviceUidConflictError(
            "A patient is already registered with this device tag."
        )


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
            mask_id(document_number),
            len(results),
        )

    return None


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------

def _find_patient_for_sync(
    db: Session, frontend_patient_id: str, device_uid: Optional[str]
) -> Optional[Patient]:
    """
    Resolve the single global patient record a sync should merge into.

    Patients are global: the same child is one shared record across every
    organization, so identity is no longer scoped by ``organization_id``.
    Resolution order:

      1. ``frontend_patient_id`` — this device's own prior record. Covers
         re-syncs and bracelet replacement (where the incoming ``device_uid``
         is new), so the existing record is updated in place rather than
         duplicated, and a device pointing its own patient at a tag owned by
         someone else is handled downstream by the replacement guard.
      2. ``device_uid`` — the hardware tag. A different organization scanning
         the same child's bracelet resolves to the existing record and merges
         into it (an identity guard in ``create_or_update_patient`` refuses the
         merge if the tag presents a different identity document).

    Returns None only when neither signal matches — a genuinely new patient.
    """
    existing = (
        db.query(Patient)
        .filter(Patient.frontend_patient_id == frontend_patient_id)
        .first()
    )
    if existing is not None:
        return existing
    if device_uid:
        return get_patient_by_device_uid(db, device_uid)
    return None


def get_existing_history_count(
    db: Session, frontend_patient_id: str, device_uid: Optional[str]
) -> Optional[int]:
    """
    Return the number of medicalHistory entries already stored for the global
    patient this sync would target, or None if the patient does not exist yet.

    Uses the same identity resolution as the sync upsert (see
    ``_find_patient_for_sync``) so the nurse-restriction check counts against
    the same record the write will target — including a record first registered
    by a different organization.

    This is a lightweight lookup used to enforce role-based restrictions
    before any expensive processing (e.g. LLM diagnosis extraction) runs.
    """
    existing = _find_patient_for_sync(db, frontend_patient_id, device_uid)
    if not existing:
        return None
    return len((existing.full_record_json or {}).get("medicalHistory", []) or [])

def create_or_update_patient(
    db: Session,
    patient_in: PatientFullRecord,
    org_id: str,
    actor_user_id: Optional[str] = None,
) -> tuple["Patient", list[str], str, bool]:
    """
    Persists patient data into the local PostgreSQL database.

    Returns:
        Tuple of (Patient instance, synced_encounter_ids, old_bg_hash, rda_paciente_sent).

        The caller uses synced_encounter_ids to determine which visits
        are new, and old_bg_hash to decide whether the
        RDA-Paciente bundle needs regeneration.
    """

    # Resolve the single global patient this sync belongs to. Patients are
    # shared across organizations, so identity is the mobile-generated
    # frontend_patient_id first (this device's own record) and the hardware
    # tag (device_uid) second — a second organization scanning the same
    # bracelet merges into the existing record instead of colliding on the
    # unique device_uid.
    existing_patient = _find_patient_for_sync(
        db, patient_in.patientId, patient_in.device_uid
    )

    # Cross-organization merge guard. When the match came from the hardware
    # tag rather than this device's own record (frontend_patient_id differs),
    # the tag identifies a patient first registered elsewhere. Confirm it is
    # the same child before merging: a tag presenting a different identity
    # document is refused rather than silently merged into the wrong record.
    if (
        existing_patient is not None
        and existing_patient.frontend_patient_id != patient_in.patientId
    ):
        incoming_doc = patient_in.patientInfo.identification.documentNumber
        if (
            existing_patient.document_number
            and incoming_doc
            and existing_patient.document_number != incoming_doc
        ):
            logger.warning(
                "Sync tag identity mismatch existing_ref=%s device=%s",
                safe_patient_ref(existing_patient.id),
                mask_id(patient_in.device_uid or ""),
            )
            raise DeviceUidConflictError(
                "This device tag is registered to a patient with a "
                "different identity document."
            )

    # Serialize the full JSON once to ensure consistency
    new_record_dump = patient_in.model_dump(mode="json")

    # retiredDeviceReason is a transport-only signal for bracelet re-labeling;
    # it must never be persisted into the authoritative clinical record or
    # echoed back on /scan. Drop it before hashing, merging or storing.
    new_record_dump.pop("retiredDeviceReason", None)

    # Compute the new background hash
    new_bg_hash = compute_background_hash(new_record_dump)

    if existing_patient:
        logger.info("Updating existing patient: %s", safe_patient_ref(existing_patient.id))
        old_record_dump = existing_patient.full_record_json
        synced_encounter_ids = list(existing_patient.synced_encounter_ids or [])
        old_bg_hash = existing_patient.background_data_hash or ""
        rda_paciente_sent = existing_patient.rda_paciente_sent

        # RULE 1: PROTECT IMMUTABLE FIELDS (Name, DOB, document, sex)
        new_record_dump["patientInfo"]["firstName"] = old_record_dump["patientInfo"]["firstName"]
        new_record_dump["patientInfo"]["firstLastName"] = old_record_dump["patientInfo"]["firstLastName"]
        new_record_dump["patientInfo"]["secondLastName"] = old_record_dump["patientInfo"].get("secondLastName")
        new_record_dump["patientInfo"]["secondName"] = old_record_dump["patientInfo"].get("secondName")
        new_record_dump["patientInfo"]["dob"] = old_record_dump["patientInfo"]["dob"]
        new_record_dump["patientInfo"]["biologicalSex"] = old_record_dump["patientInfo"]["biologicalSex"]
        # new_record_dump["patientInfo"]["bloodType"] = old_record_dump["patientInfo"].get("bloodType")
        new_record_dump["patientInfo"]["identification"] = old_record_dump["patientInfo"]["identification"]

        # RULE 2: UPDATE ONLY ALLOWED FIELDS (guardian, address, vaccines)
        existing_patient.guardian_name = patient_in.guardianInfo.name
        existing_patient.guardian_phone = patient_in.guardianInfo.phone

        # Persist guardian2 name to relational column for search if present
        if patient_in.guardian2Info and patient_in.guardian2Info.name:
            existing_patient.guardian2_name = patient_in.guardian2Info.name

        # RULE 3: MERGE CLINICAL LISTS BY UUID
        # Prevents data loss when multiple devices sync different visits
        # or vaccinations for the same patient.
        merged_record = merge_patient_records(old_record_dump, new_record_dump)
        existing_patient.full_record_json = merged_record

        # Update background hash
        # Recompute hash on merged record since immutable fields were restored
        existing_patient.background_data_hash = compute_background_hash(merged_record)

        # Single retirement reason for this sync — applies to every device UID
        # (patient bracelet and/or guardian cards) that changes in this call.
        retire_reason = (
            patient_in.retiredDeviceReason.value
            if patient_in.retiredDeviceReason is not None
            else "replaced"
        )

        # BRACELET REPLACEMENT (Update device_uid)
        if patient_in.device_uid and patient_in.device_uid != existing_patient.device_uid:
            # Reject early if the new tag already belongs to another patient.
            _ensure_device_uid_available(
                db, patient_in.device_uid, exclude_patient_id=existing_patient.id
            )
            # Retire the old tag before overwriting it, so the previous UID is
            # never silently lost. The row is added to this same transaction and
            # commits atomically with the device_uid change below — if the commit
            # races into an IntegrityError, the retirement rolls back with it.
            old_device_uid = existing_patient.device_uid
            db.add(
                RetiredDeviceUid(
                    device_uid=old_device_uid,
                    patient_id=existing_patient.id,
                    reason=retire_reason,
                    retired_by=actor_user_id,
                    device_role="patient",
                )
            )
            logger.info(
                "Device tag retired and replaced for patient %s reason=%s",
                safe_patient_ref(existing_patient.id),
                retire_reason,
            )
            existing_patient.device_uid = patient_in.device_uid

        # GUARDIAN CARD REPLACEMENT — guardian UIDs live inside full_record_json,
        # so their re-labeling is persisted by the merge above; here we only
        # record the retirement of any old guardian UID for the audit ledger.
        _record_guardian_retirements(
            db,
            old_record=old_record_dump,
            patient_in=patient_in,
            patient_id=existing_patient.id,
            reason=retire_reason,
            actor_user_id=actor_user_id,
        )

        try:
            db.commit()
        except IntegrityError:
            # Safety net for the race between the pre-check above and this commit.
            db.rollback()
            raise DeviceUidConflictError(
                "A patient is already registered with this device tag."
            )
        db.refresh(existing_patient)
        return existing_patient, synced_encounter_ids, old_bg_hash, rda_paciente_sent

    else:
        # Reject early if this tag is already registered to any patient.
        _ensure_device_uid_available(db, patient_in.device_uid)

        logger.info("Creating new patient record: %s", safe_patient_ref(patient_in.patientId))
        pi = patient_in.patientInfo

        db_patient = Patient(
            # Server generates the PK; frontend ID stored separately
            frontend_patient_id=patient_in.patientId,
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
            synced_encounter_ids=[],
            background_data_hash=new_bg_hash,
            rda_paciente_sent=False,
        )

        db.add(db_patient)
        try:
            db.commit()
        except IntegrityError:
            # Safety net for the race between the pre-check above and this commit.
            db.rollback()
            raise DeviceUidConflictError(
                "A patient is already registered with this device tag."
            )
        db.refresh(db_patient)
        # New patient: no synced encounters, empty hash, not sent
        return db_patient, [], "", False
