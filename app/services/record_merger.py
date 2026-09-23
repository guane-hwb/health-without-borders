"""
Patient record merger for offline-first sync.

When multiple devices operate offline on the same patient, each builds its
own version of the patient record. On sync, the server must merge these
versions intelligently instead of overwriting one with the other.

This module merges two snapshots of a patient record:
  - server_record: the version currently stored in the database
  - incoming_record: the version sent by the syncing device

List-type clinical entities (visits and vaccinations) are merged by their
unique identifiers. Declarative data (allergies, background history) uses
the incoming version as the latest update, except that a guardian consent
signature already held by the server is preserved when the incoming payload
omits it for the SAME guardian (see merge_patient_records).

A payload that is provably older than the server copy (``stale=True``: it
still carries a bracelet or guardian card the server already retired) is
merged conservatively instead: identifiers and guardians stay as stored and
declarative lists are united, so an old offline copy cannot erase an allergy
or re-activate a lost bracelet.
"""

import logging
import uuid as _uuid
from datetime import date, datetime
from typing import Any, Callable, Dict, Hashable, List, Optional, Set

from app.core.text_normalizer import normalize_document_number
from app.schemas.patient import DERIVED_ID_PREFIX, PatientFullRecord

logger = logging.getLogger(__name__)


def merge_patient_records(
    server_record: dict,
    incoming_record: dict,
    *,
    stale: bool = False,
    keep_server_guardians: bool = False,
) -> dict:
    """
    Produce a merged patient record from the server state and an incoming
    device payload.

    The incoming record is used as the base for all declarative fields
    (patientInfo, guardianInfo, allergies, backgroundHistory). The caller
    is responsible for restoring immutable fields before calling this
    function.

    List-type clinical entities are merged by UUID:
      - medicalHistory  → keyed by encounterIdentifier
      - vaccinationRecord → keyed by vaccinationId

    Guardian consent signatures are preserved from the server copy when the
    incoming payload omits them, so a record reconstructed from a guardian
    NFC card (which is written without the signature PNG) cannot erase a
    stored signature on sync.

    ``stale``: the payload is older than the server copy. The bracelet UID and
    guardians stay as stored, and allergies / background lists become the
    union of both sides (nothing the server holds is dropped).

    ``keep_server_guardians``: the device may add clinical data but not change
    who the guardians are or which cards identify them.

    Returns:
        A new dict representing the merged patient record.
    """
    merged = dict(incoming_record)

    if stale:
        merged["device_uid"] = server_record.get("device_uid", merged.get("device_uid"))
        merged["allergies"] = _union(
            server_record.get("allergies"), incoming_record.get("allergies"),
            lambda a: (a.get("category"), _text_key(a.get("allergen"))),
        )
        merged["backgroundHistory"] = _union_background(
            server_record.get("backgroundHistory"), incoming_record.get("backgroundHistory"),
        )

    merged["medicalHistory"] = _merge_items_by_key(
        server_items=server_record.get("medicalHistory", []),
        incoming_items=incoming_record.get("medicalHistory", []),
        key="encounterIdentifier",
        entity_label="visit",
    )

    merged["vaccinationRecord"] = _merge_items_by_key(
        server_items=server_record.get("vaccinationRecord", []),
        incoming_items=incoming_record.get("vaccinationRecord", []),
        key="vaccinationId",
        entity_label="vaccination",
    )

    if stale or keep_server_guardians:
        merged["guardianInfo"] = server_record.get("guardianInfo")
        merged["guardian2Info"] = server_record.get("guardian2Info")
        return merged

    merged["guardianInfo"] = _preserve_consent_signature(
        server_guardian=server_record.get("guardianInfo"),
        incoming_guardian=incoming_record.get("guardianInfo"),
    )
    merged["guardian2Info"] = _preserve_consent_signature(
        server_guardian=server_record.get("guardian2Info"),
        incoming_guardian=incoming_record.get("guardian2Info"),
    )

    return merged


def _text_key(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _union(
    server_items: Optional[List[Dict[str, Any]]],
    incoming_items: Optional[List[Dict[str, Any]]],
    key: Callable[[Dict[str, Any]], Hashable],
) -> List[Dict[str, Any]]:
    """Server items first (they win on a key match), then the incoming extras."""
    merged: List[Dict[str, Any]] = []
    seen: Set[Hashable] = set()
    for item in list(server_items or []) + list(incoming_items or []):
        if not isinstance(item, dict):
            continue
        item_key = key(item)
        if item_key in seen:
            continue
        seen.add(item_key)
        merged.append(item)
    return merged


def _union_background(server: Any, incoming: Any) -> Any:
    if not isinstance(server, dict):
        return incoming
    if not isinstance(incoming, dict):
        return server
    merged = dict(incoming)
    merged["chronicConditions"] = _union(
        server.get("chronicConditions"), incoming.get("chronicConditions"),
        lambda c: _text_key(c.get("chronicDescription")),
    )
    merged["familyHistory"] = _union(
        server.get("familyHistory"), incoming.get("familyHistory"),
        lambda f: (_text_key(f.get("conditionDescription")), f.get("relationship")),
    )
    merged["medications"] = _union(
        server.get("medications"), incoming.get("medications"),
        lambda m: _text_key(m.get("medicationName")),
    )
    for note in ("personalHistory", "familyHistoryNotes"):
        if not merged.get(note):
            merged[note] = server.get(note)
    return merged


def _same_guardian(server_guardian: dict, incoming_guardian: dict) -> bool:
    """
    Whether two guardian blocks describe the same person, using the strongest
    identifier both sides carry: the document, else the card UID, else the name.
    """
    server_doc = normalize_document_number(server_guardian.get("documentNumber"))
    incoming_doc = normalize_document_number(incoming_guardian.get("documentNumber"))
    if server_doc and incoming_doc:
        return server_doc == incoming_doc
    server_uid = (server_guardian.get("device_uid") or "").strip()
    incoming_uid = (incoming_guardian.get("device_uid") or "").strip()
    if server_uid and incoming_uid:
        return server_uid == incoming_uid
    server_name = _text_key(server_guardian.get("name"))
    return bool(server_name) and server_name == _text_key(incoming_guardian.get("name"))


def adopt_stored_item_ids(patient: PatientFullRecord, stored_record: Optional[dict]) -> None:
    """
    Give items that arrived without an id the id of the stored item they are.

    The schema derives a stable id for id-less items (``DERIVED_ID_PREFIX``).
    When exactly one stored visit starts at the same moment — or one stored
    vaccination has the same date, vaccine and dose — the incoming item is that
    one, re-sent after an edit that dropped its id: it takes the stored id, so
    the merge recognises it instead of appending a copy. Mutates ``patient``.
    """
    if not stored_record:
        return

    stored_visits: Dict[Any, List[str]] = {}
    for visit in stored_record.get("medicalHistory") or []:
        started = _parse_datetime(visit.get("startDateTime"))
        if started is not None and visit.get("encounterIdentifier"):
            stored_visits.setdefault(started, []).append(visit["encounterIdentifier"])
    for visit in patient.medicalHistory:
        if (visit.encounterIdentifier or "").startswith(DERIVED_ID_PREFIX):
            matches = stored_visits.get(visit.startDateTime, [])
            if len(matches) == 1:
                visit.encounterIdentifier = matches[0]

    stored_vaccines: Dict[Any, List[str]] = {}
    for vaccine in stored_record.get("vaccinationRecord") or []:
        key = (_parse_date(vaccine.get("date")), vaccine.get("vaccineCode"), vaccine.get("dose"))
        if vaccine.get("vaccinationId"):
            stored_vaccines.setdefault(key, []).append(vaccine["vaccinationId"])
    for vaccine in patient.vaccinationRecord:
        if (vaccine.vaccinationId or "").startswith(DERIVED_ID_PREFIX):
            matches = stored_vaccines.get((vaccine.date, vaccine.vaccineCode, vaccine.dose), [])
            if len(matches) == 1:
                vaccine.vaccinationId = matches[0]


def _parse_datetime(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _preserve_consent_signature(
    server_guardian: Any, incoming_guardian: Any
) -> Any:
    """
    Keep the server's guardian consent signature when the incoming guardian
    omits it.

    The guardian NFC card is written without the consent signature PNG (it is
    kilobytes and unnecessary offline), so a record reconstructed from that
    card and synced back arrives with the signature stripped. Since the merge
    takes guardianInfo from the incoming payload, without this the stored
    signature would be overwritten with null.

    Only the gap is filled: a signature already present in the incoming
    payload (an online edit carrying the full record, or an offline
    re-capture) is always preserved as-is. Returns the guardian value to use
    in the merged record — a shallow copy when a signature is restored, or the
    incoming value unchanged otherwise. Does not mutate its arguments.
    """
    if not isinstance(incoming_guardian, dict) or not isinstance(
        server_guardian, dict
    ):
        return incoming_guardian

    server_consent = server_guardian.get("consent")
    if not isinstance(server_consent, dict):
        return incoming_guardian

    if not _same_guardian(server_guardian, incoming_guardian):
        # A different person. Their consent is their own: never carry the
        # previous guardian's signature over, and drop a consent block the app
        # copied from the previous guardian (same acceptance timestamp).
        incoming_consent = incoming_guardian.get("consent")
        if (
            isinstance(incoming_consent, dict)
            and incoming_consent.get("acceptedAt") == server_consent.get("acceptedAt")
        ):
            logger.warning("Merge dropped a guardian consent inherited from the previous guardian")
            return {**incoming_guardian, "consent": None}
        return incoming_guardian
    server_signature = server_consent.get("signatureBase64")
    if not server_signature:
        return incoming_guardian

    incoming_consent = incoming_guardian.get("consent")
    if isinstance(incoming_consent, dict) and incoming_consent.get(
        "signatureBase64"
    ):
        return incoming_guardian

    restored = dict(incoming_guardian)
    if isinstance(incoming_consent, dict):
        restored_consent = dict(incoming_consent)
        restored_consent["signatureBase64"] = server_signature
        restored["consent"] = restored_consent
    else:
        # Incoming has no consent block at all — carry the server's over intact
        # so the captured signature and its metadata survive the merge.
        restored["consent"] = dict(server_consent)

    logger.info(
        "Merge restored a guardian consent signature absent from the "
        "incoming payload"
    )
    return restored


def _merge_items_by_key(
    server_items: List[Dict[str, Any]],
    incoming_items: List[Dict[str, Any]],
    key: str,
    entity_label: str,
) -> List[Dict[str, Any]]:
    """
    Merge two lists of items using a unique key field.

    Rules:
      - Items present on both sides (same key) → server version is kept
        because it's the source of truth for already-persisted data.
      - Items only on server → preserved (prevents data loss from devices
        that haven't seen them yet).
      - Items only in incoming → appended at the end (new data from this
        device).

    Items without a key (legacy data created before UUID assignment was
    enforced) are assigned a UUID in place so they can be tracked going
    forward.
    """
    _assign_missing_keys(server_items, key)
    _assign_missing_keys(incoming_items, key)

    server_keys: Set[str] = set()
    for item in server_items:
        k = item.get(key)
        if k:
            server_keys.add(k)

    # Start with all server items to guarantee nothing is lost
    merged: List[Dict[str, Any]] = list(server_items)

    # Append incoming items that the server doesn't have yet
    added = 0
    for item in incoming_items:
        k = item.get(key)
        if k and k not in server_keys:
            merged.append(item)
            added += 1

    # Log when the merge prevented data loss
    incoming_keys = {item.get(key) for item in incoming_items if item.get(key)}
    preserved = server_keys - incoming_keys
    if preserved:
        logger.warning(
            "Merge preserved %d server-side %s(s) absent from incoming payload",
            len(preserved),
            entity_label,
        )

    if added:
        logger.info(
            "Merge added %d new %s(s) from incoming payload",
            added,
            entity_label,
        )

    return merged


def _assign_missing_keys(items: List[Dict[str, Any]], key: str) -> None:
    """Assign UUIDs to items that lack a key (legacy data migration)."""
    for item in items:
        if not item.get(key):
            item[key] = str(_uuid.uuid4())