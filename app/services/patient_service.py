import copy
import hashlib
import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.core.phi_sanitizer import mask_id, safe_patient_ref
from app.core.text_normalizer import (
    DOCUMENT_SEPARATORS,
    normalize_document_number,
    normalize_text,
    text_matches,
)
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


class IdentityMismatchError(Exception):
    """
    Raised when a sync resolves to an existing patient through the bracelet
    UID alone (the payload's ``patientId`` is not that record's) and the
    payload does not prove it is the same person. The UID is readable by any
    NFC phone, so it is not enough to merge into — let alone to change the
    guardians of — a child's record. Mapped to ``409 identity_mismatch``.
    """


class DeviceRetiredError(Exception):
    """
    Raised when a NEW patient would be registered on a bracelet UID the
    server already retired (lost, damaged or replaced). Mapped to
    ``409 device_retired``.
    """


class InvalidPatientDataError(Exception):
    """
    Raised when the database rejects a value the schema let through — a string
    longer than its column, for example. It is the client's data that is wrong,
    so the API layer maps this to a ``422`` rather than a ``500`` that the app
    would retry forever.
    """


class DuplicateIdentityError(Exception):
    """
    Raised when a sync would create a *second* patient record for an identity
    document (type + number) that is already registered under another
    ``device_uid``.

    ``device_uid`` only catches a bracelet being reused. A child who lost their
    bracelet and is registered from scratch in the next clinic arrives with a
    new tag *and* a new mobile-generated ``patientId``, so nothing collides and
    the same person silently ends up with two records and a split clinical
    history. The API layer maps this to a ``409 Conflict``, so the app keeps the
    record pending instead of duplicating the patient.
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


# Maximum rows pulled for in-Python name matching. ``document_number`` +
# ``birth_date`` is an identity pair, so real candidate sets are 1 row; the cap
# only bounds the damage of dirty data (duplicate registrations) and never
# widens access — more than one surviving match is denied anyway.
_CANDIDATE_LIMIT = 10


def _normalized_document_column(column):
    """
    SQL expression that reduces a stored document number to the same canonical
    form as ``normalize_document_number``, so "VZ-9876543" is found by
    "vz 9876543". Uses only ``lower``/``replace``, which behave identically on
    PostgreSQL and on the SQLite used by the test suite.

    Diacritics are not stripped here (no portable SQL primitive does it) —
    identity document numbers are alphanumeric, so there is nothing to strip.
    """
    expression = func.lower(column)
    for separator in DOCUMENT_SEPARATORS:
        expression = func.replace(expression, separator, "")
    return expression


# Document types that stand in for "no usable identity document" (adulto/menor
# sin identificar, sin identificación). Their number is a placeholder assigned
# at registration, so two records sharing one is not evidence of the same
# person — the duplicate-identity guard skips them.
UNIDENTIFIED_DOCUMENT_TYPES = frozenset({"AS", "MS", "SI"})


def get_patient_by_document(
    db: Session, document_type: Optional[str], document_number: Optional[str]
) -> Optional[Patient]:
    """
    Fetch the patient registered under an identity document, or None.

    The number is compared in canonical form (see ``normalize_document_number``)
    so "VZ-9876543" and "vz 9876543" resolve to the same person. The type is
    compared literally: it is a closed code list (``DocumentType``) persisted
    from the enum value, so there is nothing to normalize.
    """
    normalized = normalize_document_number(document_number)
    if not document_type or not normalized:
        return None
    return (
        db.query(Patient)
        .filter(
            Patient.document_type == document_type,
            _normalized_document_column(Patient.document_number) == normalized,
        )
        .first()
    )


def _ensure_identity_available(
    db: Session,
    document_type: Optional[str],
    document_number: Optional[str],
    exclude_patient_id: Optional[str] = None,
) -> None:
    """
    Guard against registering the same person twice under different bracelets.

    Raises ``DuplicateIdentityError`` if the identity document (type + number)
    already belongs to another patient record. Only the creation path needs
    this: on update the identification block is immutable, so an existing
    record can never take over someone else's document.

    Placeholder document types (``UNIDENTIFIED_DOCUMENT_TYPES``) are skipped —
    their numbers are assigned locally at registration and say nothing about who
    the patient is, so enforcing uniqueness on them would block every unnamed
    patient after the first.
    """
    if not document_type or document_type in UNIDENTIFIED_DOCUMENT_TYPES:
        return
    owner = get_patient_by_document(db, document_type, document_number)
    if owner is not None and owner.id != exclude_patient_id:
        logger.warning(
            "Identity document already registered existing_ref=%s type=%s doc=%s",
            safe_patient_ref(owner.id),
            document_type,
            mask_id(document_number or ""),
        )
        raise DuplicateIdentityError(
            "A patient is already registered with this identity document."
        )


def _stored_given_names(patient: Patient) -> tuple[Optional[str], ...]:
    """
    Every given name on record for a patient.

    ``first_name`` is mirrored in a relational column, but the second given name
    only lives in the authoritative ``full_record_json`` payload, and clinicians
    type what the document shows ("Santiago Andrés"). Reading it here keeps both
    names searchable without a schema migration. The payload is patient-supplied
    JSON, so every step is defensive.
    """
    second_name = None
    record = patient.full_record_json
    if isinstance(record, dict):
        patient_info = record.get("patientInfo")
        if isinstance(patient_info, dict):
            second_name = patient_info.get("secondName")
    return (patient.first_name, second_name)


def _identity_matches(
    patient: Patient,
    first_name: str,
    last_name: str,
    guardian_name: Optional[str],
) -> bool:
    """
    Confirm a candidate row against the typed names, tolerating accents, case
    and partial entry (see ``text_matches``).
    """
    if not text_matches(first_name, *_stored_given_names(patient)):
        return False

    if not text_matches(last_name, patient.last_name, patient.second_last_name):
        return False

    if guardian_name and not text_matches(
        guardian_name, patient.guardian_name, patient.guardian2_name
    ):
        return False

    return True


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

    All four mandatory parameters must match for a result to be returned. This
    prevents accidental exposure of patient data and complies with Ley 1581 de
    2012 (Habeas Data) and Resolución 1888/2025 privacy requirements.

    Identity is pinned by ``document_number`` + ``birth_date``; the names are a
    confirmation step, so they are compared on standardized text instead of
    literally — the same child is registered as "Andrés Guerrero" in one clinic
    and typed as "andres guerrero" in the next, and must still be found.

    Matching rules:
      - document_number: exact match, ignoring case and the optional separators
                         in ``DOCUMENT_SEPARATORS`` ("vz 987.6543" == "VZ-9876543")
      - birth_date:      exact match
      - first_name:      partial match against the patient's given names
                         (first and second), ignoring case and accents
      - last_name:       partial match against the patient's last names — one
                         or both, in any order ("Guerrero", "Duque Guerrero")
      - guardian_name:   if provided, partial match against either guardian

    Returns None if zero or more than one patient matches (ambiguous = denied).
    """
    candidates = (
        db.query(Patient)
        .filter(
            _normalized_document_column(Patient.document_number)
            == normalize_document_number(document_number),
            Patient.birth_date == birth_date,
        )
        .limit(_CANDIDATE_LIMIT)
        .all()
    )

    results = [
        patient
        for patient in candidates
        if _identity_matches(patient, first_name, last_name, guardian_name)
    ]

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
    db: Session,
    frontend_patient_id: str,
    device_uid: Optional[str],
    *,
    lock: bool = False,
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

    ``lock=True`` takes a row lock (``SELECT … FOR UPDATE``) held until the
    caller commits, so two syncs of the same patient are serialised: the second
    one merges on top of the first instead of overwriting it with a merge of
    the same stale read (a lost update that dropped a confirmed visit).
    """
    query = db.query(Patient)
    if lock:
        query = query.with_for_update()
    existing = query.filter(Patient.frontend_patient_id == frontend_patient_id).first()
    if existing is not None:
        return existing
    if device_uid:
        return query.filter(Patient.device_uid == device_uid).first()
    return None


def _retired_uids(db: Session, uids: set[str]) -> set[str]:
    """The subset of ``uids`` present in the retirement ledger."""
    uids = {uid for uid in uids if uid}
    if not uids:
        return set()
    rows = (
        db.query(RetiredDeviceUid.device_uid)
        .filter(RetiredDeviceUid.device_uid.in_(uids))
        .distinct()
        .all()
    )
    return {row[0] for row in rows}


def _guardians_differ(stored_record: Optional[dict], patient_in: PatientFullRecord) -> bool:
    """Whether the payload names other guardians, or other cards, than stored."""
    def identity(guardian) -> tuple:
        if not guardian:
            return (None, None, None)
        return (
            normalize_text(guardian.get("name") or ""),
            (guardian.get("device_uid") or "").strip(),
            normalize_document_number(guardian.get("documentNumber")),
        )

    stored = stored_record or {}
    incoming = patient_in.model_dump(mode="json", include={"guardianInfo", "guardian2Info"})
    return any(
        identity(stored.get(slot)) != identity(incoming.get(slot))
        for slot in ("guardianInfo", "guardian2Info")
    )


def _same_person(existing: Patient, patient_in: PatientFullRecord) -> bool:
    """
    Whether a payload that reached ``existing`` through its bracelet UID is the
    same child.

    If the stored patient has a real identity document, the payload must carry
    the same number — an empty one no longer passes (it used to, which let
    anyone who read the bracelet UID take over the record). Only when the
    stored patient has no real document (none, or a placeholder type such as
    AS/MS/SI whose numbers are local) does it fall back to birth date plus given
    name and first surname.
    """
    identification = patient_in.patientInfo.identification
    existing_doc = normalize_document_number(existing.document_number)
    if existing_doc and (existing.document_type or "") not in UNIDENTIFIED_DOCUMENT_TYPES:
        return normalize_document_number(identification.documentNumber) == existing_doc
    info = patient_in.patientInfo
    return (
        existing.birth_date == info.dob
        and normalize_text(existing.first_name or "") == normalize_text(info.firstName)
        and normalize_text(existing.last_name or "") == normalize_text(info.firstLastName)
    )


def get_stored_record_for_sync(
    db: Session, frontend_patient_id: str, device_uid: Optional[str]
) -> Optional[dict]:
    """
    Return a copy of the stored record a sync would merge into, or None if the
    patient does not exist yet.

    Uses the same identity resolution as the upsert (``_find_patient_for_sync``)
    so the checks that run before any expensive work — which visits are new,
    whether a nurse is adding clinical history, which background items are
    already coded — look at the same record the write will target, including
    one first registered by a different organization.
    """
    existing = _find_patient_for_sync(db, frontend_patient_id, device_uid)
    if not existing:
        return None
    return copy.deepcopy(existing.full_record_json or {})


def create_or_update_patient(
    db: Session,
    patient_in: PatientFullRecord,
    org_id: str,
    actor_user_id: Optional[str] = None,
    conflicts: Optional[list[str]] = None,
) -> tuple["Patient", list[str], str, bool]:
    """
    Persists patient data into the local PostgreSQL database.

    Returns:
        Tuple of (Patient instance, synced_encounter_ids, old_bg_hash, rda_paciente_sent).

        The caller uses synced_encounter_ids to determine which visits
        are new, and old_bg_hash to decide whether the
        RDA-Paciente bundle needs regeneration.

    ``conflicts``, when given, receives a code for every part of the payload
    that was deliberately not applied (see ``PatientSyncResponse.conflicts``).
    """
    if conflicts is None:
        conflicts = []

    # Resolve the single global patient this sync belongs to. Patients are
    # shared across organizations, so identity is the mobile-generated
    # frontend_patient_id first (this device's own record) and the hardware
    # tag (device_uid) second — a second organization scanning the same
    # bracelet merges into the existing record instead of colliding on the
    # unique device_uid.
    existing_patient = _find_patient_for_sync(
        db, patient_in.patientId, patient_in.device_uid, lock=True
    )

    # Merge-by-tag guard. When the match came from the hardware tag rather than
    # this device's own record (frontend_patient_id differs), the payload was
    # built from scratch for a bracelet that already belongs to someone. The
    # UID alone is public, so the payload must prove it is the same child —
    # an empty document no longer passes — and even then it may add clinical
    # data but not replace the guardians or their cards (the guardian card is
    # the second factor for reading a minor's record).
    resolved_by_tag = (
        existing_patient is not None
        and existing_patient.frontend_patient_id != patient_in.patientId
    )
    if resolved_by_tag and not _same_person(existing_patient, patient_in):
        logger.warning(
            "Sync tag identity mismatch existing_ref=%s device=%s",
            safe_patient_ref(existing_patient.id),
            mask_id(patient_in.device_uid or ""),
        )
        raise IdentityMismatchError(
            "This device tag is registered to a patient with a different identity."
        )
    if resolved_by_tag and _guardians_differ(existing_patient.full_record_json, patient_in):
        conflicts.append("guardians_not_changed_by_tag_resolved_sync")

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

        # A payload that still carries a bracelet or guardian card the server
        # has already retired is, by construction, older than the stored
        # record: an offline copy from before a replacement. Applying it
        # "last write wins" re-activated the lost bracelet and erased what was
        # recorded since (an allergy, the new guardian card). Keep the stored
        # identifiers and guardians, unite the declarative lists, and still
        # take the new visits / vaccinations it brings.
        incoming_guardian_uids = {
            (patient_in.guardianInfo.device_uid or "").strip(),
            ((patient_in.guardian2Info.device_uid if patient_in.guardian2Info else None) or "").strip(),
        }
        stored_guardian_uids = {
            ((old_record_dump.get("guardianInfo") or {}).get("device_uid") or "").strip(),
            ((old_record_dump.get("guardian2Info") or {}).get("device_uid") or "").strip(),
        }
        changed_uids = incoming_guardian_uids - stored_guardian_uids
        if patient_in.device_uid != existing_patient.device_uid:
            changed_uids.add(patient_in.device_uid)
        stale = bool(_retired_uids(db, changed_uids))
        if stale:
            logger.warning(
                "Stale sync payload (carries a retired device UID) patient=%s",
                safe_patient_ref(existing_patient.id),
            )
            conflicts.append("stale_payload_retired_device_uid")
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

        keep_guardians = stale or resolved_by_tag

        # RULE 2: UPDATE ONLY ALLOWED FIELDS (guardian, address, vaccines)
        if not keep_guardians:
            existing_patient.guardian_name = patient_in.guardianInfo.name
            existing_patient.guardian_phone = patient_in.guardianInfo.phone

            # Persist guardian2 name to relational column for search if present
            if patient_in.guardian2Info and patient_in.guardian2Info.name:
                existing_patient.guardian2_name = patient_in.guardian2Info.name

        # RULE 3: MERGE CLINICAL LISTS BY UUID
        # Prevents data loss when multiple devices sync different visits
        # or vaccinations for the same patient.
        merged_record = merge_patient_records(
            old_record_dump,
            new_record_dump,
            stale=stale,
            keep_server_guardians=keep_guardians,
        )
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

        # BRACELET REPLACEMENT (Update device_uid) — never from a stale payload
        if (
            not stale
            and patient_in.device_uid
            and patient_in.device_uid != existing_patient.device_uid
        ):
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
        if not keep_guardians:
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
        except DataError:
            db.rollback()
            raise InvalidPatientDataError("A value does not fit its database column.")
        db.refresh(existing_patient)
        return existing_patient, synced_encounter_ids, old_bg_hash, rda_paciente_sent

    else:
        # Reject early if this tag is already registered to any patient.
        _ensure_device_uid_available(db, patient_in.device_uid)
        # ...or if it was retired: a lost or replaced bracelet no longer
        # belongs to HWB and must not identify a new child.
        if _retired_uids(db, {patient_in.device_uid}):
            raise DeviceRetiredError("This device tag was retired.")

        pi = patient_in.patientInfo
        # Reject just as early if this person is already on record under a
        # different bracelet: a new tag plus a new frontend_patient_id collides
        # on nothing, so without this the same child gets a second record and a
        # split clinical history.
        _ensure_identity_available(
            db,
            pi.identification.documentType.value,
            pi.identification.documentNumber,
        )

        logger.info("Creating new patient record: %s", safe_patient_ref(patient_in.patientId))

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
        except DataError:
            db.rollback()
            raise InvalidPatientDataError("A value does not fit its database column.")
        db.refresh(db_patient)
        # New patient: no synced encounters, empty hash, not sent
        return db_patient, [], "", False
