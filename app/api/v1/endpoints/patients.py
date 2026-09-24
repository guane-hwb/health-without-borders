import asyncio
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import (
    DEVICE_RETIRED,
    DEVICE_UID_CONFLICT,
    DUPLICATE_IDENTITY,
    GUARDIAN_MISMATCH,
    GUARDIAN_REQUIRED,
    IDENTITY_MISMATCH,
    ApiError,
)
from app.core.phi_sanitizer import mask_id
from app.core.rate_limit import limiter
from app.db.models import User, UserRole
from app.db.session import get_db
from app.schemas.emergency_access import (
    EmergencyAccessSyncRequest,
    EmergencyAccessSyncResponse,
)
from app.schemas.nfc_key_version import (
    NfcKeyRevokeRequest,
    NfcKeyRevokeResponse,
    NfcKeyringStatusResponse,
    NfcKeyRotateRequest,
    NfcKeyRotateResponse,
    NfcKeyVersionSyncRequest,
    NfcKeyVersionSyncResponse,
    NfcKeyVersionUsageResponse,
)
from app.schemas.patient import (
    CodeSource,
    PatientFullRecord,
    PatientSearchRequest,
    PatientSyncRecord,
    PatientSyncResponse,
)
from app.services.emergency_access_service import store_emergency_access_entries
from app.services.fhir import fhir_backend
from app.services.fhir_service import convert_to_fhir_rda
from app.services.llm import medical_llm_processor
from app.services.llm.base import ai_code_source, mark_ai_diagnoses
from app.services.nfc_key_service import (
    keyring_status,
    revoke_version,
    rotate_to_new_version,
)
from app.services.nfc_key_version_service import (
    store_key_version_observations,
    summarize_key_version_usage,
)
from app.services.patient_service import (
    DeviceRetiredError,
    DeviceUidConflictError,
    DuplicateIdentityError,
    IdentityMismatchError,
    InvalidPatientDataError,
    compute_background_hash,
    create_or_update_patient,
    find_patient_strict,
    get_patient_by_device_uid,
    get_retired_device_uid,
    get_stored_record_for_sync,
    record_fhir_delivery,
)
from app.services.record_merger import adopt_stored_item_ids

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/scan/{device_uid}", response_model=PatientFullRecord, status_code=status.HTTP_200_OK)
async def get_patient_by_device_uid_scan(
    device_uid: str, 
    guardian_device_uid: Optional[str] = Header(None, alias="X-Guardian-Device-UID", description="Scanned ID from guardian's bracelet"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve a patient's full medical record by scanning their NFC tag or barcode.

    **Guardian 2FA for minors:**
    If the patient is under 18 years old, the `X-Guardian-Device-UID` header
    becomes mandatory. The scanned guardian tag must match the one registered in the
    patient's record. Access is denied if they do not match. The value travels in a
    header (not the URL) so it does not leak into access logs, proxies, or browser history.

    **Parameters:**
    - `device_uid` (path): The hardware identifier scanned from the patient's bracelet.
    - `X-Guardian-Device-UID` (header, conditional): Required only if patient is a minor.

    **Allowed roles:** `doctor`, `nurse`.

    **Responses:**
    - `200`: Full patient record returned.
    - `403`: Caller is not `doctor` or `nurse`.
    - `403`: Patient is a minor and the `X-Guardian-Device-UID` header was not provided.
    - `403`: Patient is a minor and guardian tag does not match.
    - `404`: No patient registered with that device UID.
    - `410`: The tag was retired (lost/damaged/replaced) and no longer belongs to
      HWB. Body: `{"code": "device_retired", "reason": ..., "message": ...}`.
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
        mask_id(device_uid),
    )
    
    # Wrap synchronous DB call in to_thread to avoid blocking the event loop
    patient_db = await asyncio.to_thread(get_patient_by_device_uid, db, device_uid)
    
    if not patient_db:
        # Distinguish a retired bracelet (lost/damaged/replaced) from a genuinely
        # unknown tag, so the app can tell the user the bracelet was retired
        # instead of showing a blank/unknown-chip error.
        retired = await asyncio.to_thread(get_retired_device_uid, db, device_uid)
        if retired is not None:
            logger.info(
                "Scan of retired device tag org_id=%s device_ref=%s reason=%s",
                current_user.organization_id,
                mask_id(device_uid),
                retired.reason,
            )
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={
                    "code": "device_retired",
                    "reason": retired.reason,
                    "message": (
                        "This bracelet has been retired and no longer "
                        "belongs to HWB."
                    ),
                },
            )
        logger.warning(
            "Patient scan not found org_id=%s device_ref=%s",
            current_user.organization_id,
            mask_id(device_uid),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found."
        )
    
    # Calculate patient's age to determine if guardian authentication is required
    today = date.today()
    dob = patient_db.birth_date
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    # If patient is a minor, require guardian device UID and validate it against the stored guardian info
    if age < 18:
        if not guardian_device_uid:
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "Guardian bracelet scan required for minors.",
                code=GUARDIAN_REQUIRED,
            )
        
        # Extract the stored guardian device UIDs from the patient's full record JSON
        stored_guardian_uid = patient_db.full_record_json.get("guardianInfo", {}).get("device_uid")
        stored_guardian2_uid = patient_db.full_record_json.get("guardian2Info", {}).get("device_uid") if patient_db.full_record_json.get("guardian2Info") else None

        # Accept either guardian's tag
        valid_uids = {uid for uid in [stored_guardian_uid, stored_guardian2_uid] if uid}
        if guardian_device_uid not in valid_uids:
            logger.warning(
                "Guardian validation failed actor_id=%s org_id=%s patient_ref=%s",
                current_user.id,
                current_user.organization_id,
                mask_id(patient_db.id),
            )
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "Guardian tag mismatch. Access denied.",
                code=GUARDIAN_MISMATCH,
            )

    return patient_db.full_record_json


def _normalized_text(value: Optional[str]) -> str:
    return " ".join((value or "").split()).casefold()


def _code_source(value: Optional[str]) -> Optional[CodeSource]:
    """Stored JSON holds the enum's value; legacy items have none."""
    return CodeSource(value) if value else None


async def _apply_llm_coding(
    patient_data: PatientFullRecord,
    new_visits: list,
    stored_record: Optional[dict],
) -> None:
    """
    Fill in the codes the app does not send, without touching what it did send.

    - Diagnoses: only NEW visits without a diagnosis go to the LLM; visits the
      server already holds keep their stored diagnosis in the merge, so asking
      again only re-sent clinical notes to Vertex and threw the answer away.
      Every AI diagnosis is stamped with its provenance; a diagnosis the client
      sent on a new visit is the clinician's.
    - Background (family history, chronic conditions): the clinician's text is
      never replaced. The code and its display go to separate fields. An item
      whose text is already coded on the server reuses that coding instead of
      calling the LLM again.
    """
    model = getattr(medical_llm_processor, "model_name", "unknown")

    for visit in new_visits:
        if visit.diagnosis:
            for diagnosis in visit.diagnosis:
                if diagnosis.source is None:
                    diagnosis.source = CodeSource.CLINICIAN
            continue
        evaluation = visit.clinicalEvaluation
        diagnoses = await asyncio.to_thread(
            medical_llm_processor.extract_diagnoses,
            history=evaluation.historyOfCurrentIllness,
            physical=evaluation.generalPhysicalExamination,
            systems=evaluation.systemsExamination,
            plan=evaluation.treatmentPlanObservations,
        )
        visit.diagnosis = mark_ai_diagnoses(diagnoses, model)

    background = patient_data.backgroundHistory
    if background is None:
        return
    stored_background = (stored_record or {}).get("backgroundHistory") or {}

    stored_family = {
        (_normalized_text(item.get("conditionDescription")), item.get("relationship")): item
        for item in stored_background.get("familyHistory") or []
        if item.get("conditionCie10Code")
    }
    for item in background.familyHistory:
        if not item.conditionDescription or item.conditionCie10Code:
            continue
        known = stored_family.get(
            (_normalized_text(item.conditionDescription), item.relationship.value)
        )
        if known is not None:
            item.conditionCie10Code = known.get("conditionCie10Code")
            item.conditionCie11Code = known.get("conditionCie11Code")
            item.conditionCodedDisplay = known.get("conditionCodedDisplay")
            item.codingSource = _code_source(known.get("codingSource"))
            continue
        coded = await asyncio.to_thread(
            medical_llm_processor.code_family_history_item, item.conditionDescription
        )
        item.conditionCie10Code = coded.get("icd10Code")
        item.conditionCie11Code = coded.get("icd11Code")
        item.conditionCodedDisplay = coded.get("description")
        item.codingSource = ai_code_source(item.conditionCie10Code)

    stored_chronic = {
        _normalized_text(item.get("chronicDescription")): item
        for item in stored_background.get("chronicConditions") or []
        if item.get("chronicCie10Code")
    }
    for cc_item in background.chronicConditions:
        if not cc_item.chronicDescription or cc_item.chronicCie10Code:
            continue
        known = stored_chronic.get(_normalized_text(cc_item.chronicDescription))
        if known is not None:
            cc_item.chronicCie10Code = known.get("chronicCie10Code")
            cc_item.chronicCie11Code = known.get("chronicCie11Code")
            cc_item.chronicCodedDisplay = known.get("chronicCodedDisplay")
            cc_item.codingSource = _code_source(known.get("codingSource"))
            continue
        coded = await asyncio.to_thread(
            medical_llm_processor.code_chronic_condition, cc_item.chronicDescription
        )
        cc_item.chronicCie10Code = coded.get("icd10Code")
        cc_item.chronicCie11Code = coded.get("icd11Code")
        cc_item.chronicCodedDisplay = coded.get("description")
        cc_item.codingSource = ai_code_source(cc_item.chronicCie10Code)


@router.post("/sync", response_model=PatientSyncResponse, status_code=status.HTTP_201_CREATED)
async def sync_patient(
    patient_data: PatientSyncRecord, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Synchronize a patient record from the mobile app to the cloud.

    **Process flow:**
    1. If any visit NEW to the server is missing a diagnosis, the clinical
       evaluation text is analyzed by a medical LLM (Gemini) to suggest one.
       AI diagnoses carry `source`, `model` and `generatedAt` and go to the
       RDA as `provisional`.
    2. If a `familyHistory` / `chronicConditions` item lacks ICD codes, the
       server reuses the coding it already stored for the same text or asks the
       LLM. The professional's text is never replaced: the code's name goes to
       `conditionCodedDisplay` / `chronicCodedDisplay`.
    3. The record is persisted or updated in PostgreSQL.
    4. Only NEW FHIR RDA Bundles are generated (delta logic):
       - RDA-Paciente: on first sync or when background data changes.
       - RDA-Consulta: only for visits not previously sent, identified by
         encounterIdentifier UUID.
    5. Bundles are transmitted to the Google Cloud Healthcare API.
    6. Sync tracking is updated in the database.

    **Nurse restriction:** A `nurse` may call this endpoint to append vaccination records,
    but cannot add visits the server does not already hold — neither to an
    existing patient nor when creating one. Attempts to do so return `403`.

    **Device tag conflict:** If the record's `device_uid` is already registered to a
    different patient, the endpoint returns `409` and no record is created or modified.

    **Duplicate identity:** If the record would create a NEW patient whose identity
    document (`documentType` + `documentNumber`) already belongs to another record,
    the endpoint returns `409` and nothing is created — the same person must not be
    registered twice under two different `device_uid`s. Unidentified document types
    (`AS`, `MS`, `SI`) are exempt, since their numbers are local placeholders.

    **Allowed roles:** `doctor`, `nurse`.
    """
    # Read once, up front: after a failed commit the session is rolled back and
    # current_user is expired, so touching its attributes in the error handler
    # below would raise again and turn a clean 4xx/500 into an unlogged crash.
    actor_id = current_user.id
    actor_org_id = current_user.organization_id
    actor_role = current_user.role

    if actor_role not in {UserRole.doctor, UserRole.nurse}:
        logger.warning(
            "Unauthorized patient sync actor_id=%s role=%s org_id=%s",
            actor_id,
            actor_role,
            actor_org_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only doctors and nurses can create or update patient records."
        )
    
    try:
        # Look at the record this sync will merge into BEFORE any LLM work:
        # only visits and background items the server does not have yet are
        # new, and the check below and the LLM calls must use that, not the
        # size of the payload.
        stored_record = await asyncio.to_thread(
            get_stored_record_for_sync,
            db,
            patient_data.patientId,
            patient_data.device_uid,
        )
        # Release the connection: the LLM calls below can take seconds each and
        # must not hold a pooled connection "idle in transaction".
        await asyncio.to_thread(db.rollback)

        # An item the app re-sent without its id (the edit screens drop it)
        # takes the id of the stored item it is, so it is not added twice.
        adopt_stored_item_ids(patient_data, stored_record)

        stored_visit_ids = {
            visit.get("encounterIdentifier")
            for visit in (stored_record or {}).get("medicalHistory") or []
        }
        new_visits = [
            visit
            for visit in patient_data.medicalHistory
            if visit.encounterIdentifier not in stored_visit_ids
        ]

        # A nurse may append vaccines but must not add medical history. What
        # counts is whether any visit is new to the server — comparing list
        # sizes let a nurse add a visit by sending only that one, or create a
        # patient that already had visits.
        if actor_role == UserRole.nurse and new_visits:
            logger.warning(
                "Nurse attempted to add medical history actor_id=%s org_id=%s patient_ref=%s",
                actor_id,
                actor_org_id,
                mask_id(patient_data.patientId),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access Denied: Nurses can only add vaccines, not medical history.",
            )

        await _apply_llm_coding(patient_data, new_visits, stored_record)

        # 1. Save to DB (wrapped in to_thread to avoid blocking the event loop)
        logger.info(
            "Patient sync started actor_id=%s role=%s org_id=%s patient_ref=%s",
            actor_id,
            actor_role,
            actor_org_id,
            mask_id(patient_data.patientId),
        )
        conflicts: list[str] = []
        saved_patient, synced_encounter_ids, old_bg_hash, rda_paciente_sent = (
            await asyncio.to_thread(
                create_or_update_patient,
                db, patient_data, actor_org_id,
                actor_id, conflicts,
            )
        )
        
        # 2. FHIR RDA Conversion — DELTA by encounter UUID + background hash.
        # Built from the STORED record, not the payload: the payload may carry
        # identity fields the server refused, or allergies a stale copy lacked;
        # the RDA must say exactly what HWB holds.
        saved_patient_id = str(saved_patient.id)
        stored_record = saved_patient.full_record_json
        try:
            rda_source = PatientFullRecord.model_validate(stored_record)
        except ValidationError:
            logger.warning(
                "Stored record does not validate; building the RDA from the payload "
                "patient_ref=%s", mask_id(saved_patient_id),
            )
            rda_source = patient_data
        current_bg_hash = compute_background_hash(stored_record)
        # background_data_hash is the hash the FHIR Store last accepted.
        background_data_changed = rda_paciente_sent and old_bg_hash != current_bg_hash

        fhir_bundles, new_encounter_ids = convert_to_fhir_rda(
            rda_source,
            synced_encounter_ids=synced_encounter_ids,
            rda_paciente_already_sent=rda_paciente_sent,
            background_data_changed=background_data_changed,
        )
        logger.debug("Generated %d FHIR RDA Bundle(s) (delta)", len(fhir_bundles))
        # RDA-Paciente, when generated, is first; then one RDA-Consulta per id.
        bundle_kinds = [None] * (len(fhir_bundles) - len(new_encounter_ids)) + new_encounter_ids

        # Do not hold a pooled connection while waiting on the FHIR Store.
        await asyncio.to_thread(db.rollback)

        # 3. Send each Bundle to the configured FHIR Store
        fhir_status = "success" if not fhir_bundles else "unknown"
        accepted_encounter_ids: list[str] = []
        sent_background_hash = None
        for i, (bundle, encounter_id) in enumerate(zip(fhir_bundles, bundle_kinds)):
            result = await asyncio.to_thread(fhir_backend.send_bundle, bundle)
            bundle_status = result.get("status", "unknown")

            if bundle_status != "success":
                logger.warning(
                    "Patient sync FHIR warning patient_ref=%s bundle=%d status=%s",
                    mask_id(saved_patient_id),
                    i,
                    bundle_status,
                )
                fhir_status = bundle_status
                continue
            if fhir_status == "unknown":
                fhir_status = "success"
            if encounter_id is None:
                sent_background_hash = current_bg_hash
            else:
                accepted_encounter_ids.append(encounter_id)
            logger.info(
                "Patient sync FHIR success patient_ref=%s bundle=%d",
                mask_id(saved_patient_id), i
            )

        # 4. Record every accepted bundle, even if another one failed.
        await asyncio.to_thread(
            record_fhir_delivery,
            db, saved_patient_id, accepted_encounter_ids, sent_background_hash,
        )

        return PatientSyncResponse(
            status="success",
            internal_id=saved_patient_id,
            fhir_status=fhir_status,
            vida_code=None,
            message="Patient synced and processed successfully",
            conflicts=conflicts,
        )

    except HTTPException:
        raise

    except InvalidPatientDataError:
        logger.warning(
            "Patient sync rejected by the database constraints actor_id=%s org_id=%s",
            actor_id,
            actor_org_id,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The patient record contains a value the server cannot store.",
        )

    except DeviceUidConflictError:
        logger.warning(
            "Patient sync device tag conflict actor_id=%s org_id=%s",
            actor_id,
            actor_org_id,
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "A patient is already registered with this device tag.",
            code=DEVICE_UID_CONFLICT,
        )

    except IdentityMismatchError:
        logger.warning(
            "Patient sync identity mismatch on a tag-resolved record actor_id=%s org_id=%s",
            actor_id,
            actor_org_id,
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "This device tag is registered to a patient with a different identity.",
            code=IDENTITY_MISMATCH,
        )

    except DeviceRetiredError:
        logger.warning(
            "Patient sync on a retired device tag actor_id=%s org_id=%s",
            actor_id,
            actor_org_id,
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "This device tag was retired and cannot identify a new patient.",
            code=DEVICE_RETIRED,
        )

    except DuplicateIdentityError:
        logger.warning(
            "Patient sync duplicate identity document actor_id=%s org_id=%s",
            actor_id,
            actor_org_id,
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "A patient is already registered with this identity document.",
            code=DUPLICATE_IDENTITY,
        )

    except Exception:
        await asyncio.to_thread(db.rollback)
        # The formatter strips exception messages (they may embed PHI), so the
        # traceback keeps only types and frames.
        logger.exception(
            "Critical error during patient sync actor_id=%s org_id=%s",
            actor_id,
            actor_org_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal Server Error processing patient data."
        )

@router.post("/search", response_model=PatientFullRecord, status_code=status.HTTP_200_OK)
@limiter.limit(settings.RATE_LIMIT_PATIENT_SEARCH)
async def search_patient(
    request: Request,
    criteria: PatientSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Strict patient lookup by identity — returns exactly one patient or 404.

    This endpoint enforces strict matching to protect patient data privacy 
    in compliance with Ley 1581 de 2012 (Habeas Data) and Ley 1751 de 2015.
    It will **never** return a list of patients.

    Identity criteria are sent in the **request body** (not the query string) so
    that the document number, names and birth date never leak into access logs,
    proxies or browser history.

    Identity is pinned by `document_number` + `birth_date`. The names confirm
    that identity, so they are compared on **standardized text** — accents,
    letter case and extra spacing are ignored, and a partial entry is enough.
    A child registered as "Andrés Guerrero" is therefore still found when a
    clinician in the next clinic types "andres guerrero".

    **Body fields (`PatientSearchRequest`):**
    - `document_number`: Exact match against the patient's identity document,
      ignoring case and optional separators (`vz 987.6543` finds `VZ-9876543`).
    - `birth_date`: Exact match (YYYY-MM-DD).
    - `first_name`: Partial match against the patient's given names (first and
      second), accent- and case-insensitive.
    - `last_name`: Partial match against the patient's last names — send one if
      the patient has one, both if they have two, in any order. Accepted as
      `last_names` too.
    - `guardian_name` (optional): If the patient has a registered guardian,
      providing this adds an extra layer of verification. Partial match is allowed.

    **Security:**
    - If the criteria match more than one patient (ambiguous), the endpoint 
      returns 404 — it will not expose either record.

    **Allowed roles:** `doctor`, `nurse`, `org_admin`.

    **Responses:**
    - `200`: Patient record found and returned.
    - `403`: Caller is not authorized.
    - `404`: No patient found matching the provided criteria.
    - `422`: Missing or malformed body fields.
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
        mask_id(criteria.document_number),
    )

    # Wrap synchronous DB call in to_thread to avoid blocking the event loop
    patient = await asyncio.to_thread(
        find_patient_strict,
        db=db,
        document_number=criteria.document_number,
        birth_date=criteria.birth_date,
        first_name=criteria.first_name,
        last_name=criteria.last_name,
        guardian_name=criteria.guardian_name,
    )

    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No patient found matching the provided criteria."
        )

    return patient.full_record_json


@router.post(
    "/emergency-access",
    response_model=EmergencyAccessSyncResponse,
    status_code=status.HTTP_200_OK,
)
async def sync_emergency_access(
    payload: EmergencyAccessSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Sync break-glass (emergency access) audit entries from the mobile app.

    When a clinician opens a minor's record through the offline emergency path
    (without the guardian's second factor), the app records the access locally
    and syncs the pending entries here into a central, append-only audit ledger.
    This is the compensating control for `/search` access to minors' records.

    **Idempotent:** each entry carries a client-generated `client_event_id`.
    Re-sending the same entry (the local queue may retry) is a no-op — the
    server de-duplicates on that id and reports it under `duplicates`.

    **Allowed roles:** `doctor`, `nurse` (the point-of-care staff whose devices
    hold the local log).

    **Responses:**
    - `200`: Batch processed. Body reports `received`, `stored`, `duplicates`.
    - `403`: Caller is not `doctor` or `nurse`.
    - `422`: Missing or malformed body fields (e.g. empty `entries`).
    """
    if current_user.role not in {UserRole.doctor, UserRole.nurse}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only doctors and nurses can sync emergency access logs.",
        )

    logger.info(
        "Emergency access sync request actor_id=%s org_id=%s entries=%d",
        current_user.id,
        current_user.organization_id,
        len(payload.entries),
    )

    stored, duplicates = await asyncio.to_thread(
        store_emergency_access_entries,
        db,
        payload.entries,
        current_user.organization_id,
    )

    return EmergencyAccessSyncResponse(
        status="success",
        received=len(payload.entries),
        stored=stored,
        duplicates=duplicates,
    )


@router.post(
    "/nfc-key-versions",
    response_model=NfcKeyVersionSyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_nfc_key_versions(
    payload: NfcKeyVersionSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Report which NFC key version each scanned chip was found on.

    A chip gives no readable hint of which key encrypted it, so this is the only
    way to learn how far a key rotation has drained. Retiring a version makes
    every chip still on it unreadable offline; without these counts that number
    is unknowable and the decision is a guess.

    **Not idempotent, by design.** Every sighting is appended. The device
    already collapses repeat reads of one chip into a single pending row, and
    the interval between sightings is what a retention period has to be sized
    from — de-duplicating here would discard exactly that.

    **Allowed roles:** `doctor`, `nurse`, `org_admin` — everyone whose device
    holds the keyring and can therefore read a chip. `org_admin` is included
    because it reaches the patient profile through the lost-wristband flow and
    receives keys; excluding it would silently drop its observations.

    **Privacy:** entries carry a device UID, a role, a key version and a
    timestamp. No patient identifier, no clinical data, no key material.

    **Responses:**
    - `202`: Batch accepted. Body reports `received` and `stored`.
    - `403`: Caller is not `doctor` or `nurse`.
    - `422`: Missing or malformed body fields (e.g. empty `entries`).
    """
    if current_user.role not in {
        UserRole.doctor,
        UserRole.nurse,
        UserRole.org_admin,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access Denied: Only clinical staff and organization admins "
                "can report NFC key versions."
            ),
        )

    stored = store_key_version_observations(db, payload.entries, current_user)
    return NfcKeyVersionSyncResponse(
        received=len(payload.entries), stored=stored
    )


@router.get(
    "/nfc-key-versions/usage",
    response_model=NfcKeyVersionUsageResponse,
    status_code=status.HTTP_200_OK,
)
def get_nfc_key_version_usage(
    window_days: int = 90,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Summarise which key versions are still in circulation.

    Each chip is counted once, under the version of its most recent sighting —
    the version it is on now. `retirable_versions` lists versions with no
    sighting inside `window_days`.

    **Read that list as a veto, not a clearance.** A version missing from the
    telemetry may still have chips in the field whose patients have not come
    back; absence of sightings is not evidence of absence of chips. It can tell
    you a retirement is obviously unsafe. It cannot tell you one is safe.

    **Allowed roles:** `org_admin`, `superadmin` — this is an operational
    question, not a point-of-care one.

    **Responses:**
    - `200`: Summary returned.
    - `403`: Caller is not an administrator.
    - `422`: `window_days` out of range.
    """
    if current_user.role not in {UserRole.org_admin, UserRole.superadmin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only administrators can read NFC key version usage.",
        )
    if window_days < 1 or window_days > 3650:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window_days must be between 1 and 3650.",
        )

    return summarize_key_version_usage(db, window_days=window_days)


@router.post(
    "/nfc-keys/revoke",
    response_model=NfcKeyRevokeResponse,
    status_code=status.HTTP_200_OK,
)
def revoke_nfc_key(
    payload: NfcKeyRevokeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stop serving an NFC key version. **Emergency operation.**

    Use this when a key is believed to be compromised — an extracted device, a
    leaked value. The version stops being delivered to any device, for reading
    as well as writing; anything less is useless against a leak, since the
    leaked key is precisely the one that reads.

    **What it costs.** Chips written under the revoked version become
    *online-only* until they are rewritten. No data is lost: the chip UID is
    unencrypted, resolves the patient through `/patients/scan`, and the next
    save migrates the chip to the current version. If the revoked version was
    the current one, a replacement is generated and becomes current.

    **What it does not do.** Revocation stops *delivery*. A device that already
    holds the ring keeps it until its next refresh — up to an hour online, and
    up to the refresh-token window (7 days) if it is offline. There is no way
    to reach an offline device sooner.

    **Before advancing the current version**, every device must be running a
    build that understands the keyring. An older build takes the current key,
    ignores the ring, and loses the ability to read everything written under the
    previous version. `acknowledge_chip_impact` exists so this is a deliberate
    choice rather than a surprise.

    **Allowed roles:** `superadmin`.

    **Responses:**
    - `200`: Revoked. Body reports the replacement version, if one was created.
    - `400`: Already revoked, or the keyring is served from the environment
      (no `NFC_KEK` configured, so there is nothing to change at runtime).
    - `403`: Caller is not a `superadmin`.
    - `404`: No such key version.
    - `422`: Malformed body, or `acknowledge_chip_impact` not set.
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only superadmins can revoke NFC keys.",
        )
    if not payload.acknowledge_chip_impact:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "acknowledge_chip_impact must be true: revoking makes chips on "
                "this version online-only until they are rewritten."
            ),
        )

    try:
        result = revoke_version(
            db,
            version=payload.version,
            actor_id=current_user.id,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return NfcKeyRevokeResponse(**result)


@router.get(
    "/nfc-keys",
    response_model=NfcKeyringStatusResponse,
    status_code=status.HTTP_200_OK,
)
def get_nfc_keyring_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The state of the keyring: which versions exist, which is current, which are
    revoked.

    **Never returns key material** — only version numbers, status, the
    fingerprint of the KEK that wrapped each row, and timestamps.

    `source` reports where the ring comes from: `database` once `NFC_KEK` is
    configured, `environment` otherwise.

    **Allowed roles:** `superadmin`.

    **Responses:**
    - `200`: Status returned.
    - `403`: Caller is not a `superadmin`.
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only superadmins can read the NFC keyring state.",
        )
    return NfcKeyringStatusResponse(**keyring_status(db))


@router.post(
    "/nfc-keys/rotate",
    response_model=NfcKeyRotateResponse,
    status_code=status.HTTP_200_OK,
)
def rotate_nfc_key(
    payload: NfcKeyRotateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a new key version and make it current.

    Rotation limits how long a leaked key stays useful. It does **not**
    re-encrypt existing chips: older versions keep being delivered, so chips
    written under them stay readable offline, and they migrate to the new
    version as they are rewritten.

    This is the manual trigger. The same thing happens on its own once
    `NFC_AUTO_ROTATE` is enabled and the current key reaches
    `NFC_ROTATION_PERIOD_DAYS`.

    **The fleet must be ready.** Advancing the current version breaks reads on
    any device still running a build that predates the keyring: it takes the
    current key, ignores the ring, and can no longer read anything written
    under the previous version. `acknowledge_fleet_updated` exists so this is a
    deliberate act by someone who has checked.

    **Allowed roles:** `superadmin`.

    **Responses:**
    - `200`: Rotated. `new_version` is null when another instance rotated
      first, which is not an error.
    - `400`: The keyring is served from the environment (no `NFC_KEK`), it has
      not been initialised, or version 255 has been reached.
    - `403`: Caller is not a `superadmin`.
    - `422`: Malformed body, or `acknowledge_fleet_updated` not set.
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only superadmins can rotate NFC keys.",
        )
    if not payload.acknowledge_fleet_updated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "acknowledge_fleet_updated must be true: advancing the current "
                "version breaks reads on devices running an older build."
            ),
        )

    state = keyring_status(db)
    previous = state["current_version"]
    try:
        new_version = rotate_to_new_version(
            db, actor_id=current_user.id, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return NfcKeyRotateResponse(
        new_version=new_version, previous_version=previous
    )
