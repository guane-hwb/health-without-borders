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
the incoming version as the latest update.
"""

import logging
import uuid as _uuid
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)


def merge_patient_records(
    server_record: dict, incoming_record: dict
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

    Returns:
        A new dict representing the merged patient record.
    """
    merged = dict(incoming_record)

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

    return merged


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