"""
Unit tests for app/services/patient_service.py (compute_background_hash).

These tests lock in the semantics of the background-data hash after removing
the dead top-level ``"medications"`` key from the hashed fields:

  * Medications live under ``backgroundHistory.medications`` and must still
    influence the hash (so a medication change regenerates RDA-Paciente).
  * A top-level ``"medications"`` key must NOT influence the hash (the record
    has no such field; the previously hashed value was always ``None``).
  * Encounter-level data (``medicalHistory`` visits) must NOT influence the
    background hash, since visits are tracked separately by encounter id.
"""

import copy

from app.db.models import Patient
from app.services.patient_service import (
    _find_patient_for_sync,
    compute_background_hash,
)


def _record() -> dict:
    """A minimal record in the shape produced by ``model_dump(mode="json")``.

    ``compute_background_hash`` only reads a handful of top-level keys, so this
    intentionally keeps just enough structure to exercise that logic without
    constructing a fully valid PatientFullRecord.
    """
    return {
        "patientId": "uuid-1",
        "device_uid": "DEV-1",
        "patientInfo": {
            "identification": {"documentType": "TI", "documentNumber": "104"},
            "firstName": "Isabella",
            "firstLastName": "Martinez",
            "dob": "2015-08-22",
            "biologicalSex": "F",
            "bloodType": "O+",
        },
        "guardianInfo": {"name": "Maria", "relationship": "Madre"},
        "guardian2Info": None,
        "backgroundHistory": {
            "chronicConditions": [],
            "familyHistory": [],
            "medications": [
                {"medicationName": "Amoxicilina", "status": "active"},
            ],
        },
        "allergies": [],
        # Encounter-level data: deliberately present to prove it is excluded
        # from the background hash.
        "medicalHistory": [
            {"encounterIdentifier": "enc-1", "type": "Consultation"},
        ],
        "vaccinationRecord": [],
    }


def test_identical_records_produce_identical_hash():
    assert compute_background_hash(_record()) == compute_background_hash(_record())


def test_changing_a_medication_alters_background_hash():
    """Editing a medication under backgroundHistory must change the hash."""
    base = _record()
    changed = copy.deepcopy(base)
    changed["backgroundHistory"]["medications"][0]["medicationName"] = "Ibuprofeno"

    assert compute_background_hash(base) != compute_background_hash(changed)


def test_adding_a_medication_alters_background_hash():
    """Appending a new medication must change the hash."""
    base = _record()
    added = copy.deepcopy(base)
    added["backgroundHistory"]["medications"].append(
        {"medicationName": "Loratadina", "status": "active"}
    )

    assert compute_background_hash(base) != compute_background_hash(added)


def test_top_level_medications_key_is_ignored():
    """A top-level ``medications`` key must not affect the hash.

    The record schema has no top-level ``medications`` field; the removed line
    ``record.get("medications")`` always resolved to ``None``. Injecting such a
    key here must therefore be a no-op for the hash.
    """
    base = _record()
    with_dead_key = copy.deepcopy(base)
    with_dead_key["medications"] = [{"medicationName": "Cualquiera"}]

    assert compute_background_hash(base) == compute_background_hash(with_dead_key)


def test_adding_a_visit_does_not_alter_background_hash():
    """Encounter-level changes (new visit) must not change the background hash."""
    base = _record()
    with_visit = copy.deepcopy(base)
    with_visit["medicalHistory"].append(
        {"encounterIdentifier": "enc-2", "type": "Consultation"}
    )

    assert compute_background_hash(base) == compute_background_hash(with_visit)


def test_changing_an_allergy_alters_background_hash():
    """Allergies are part of the background and must change the hash."""
    base = _record()
    changed = copy.deepcopy(base)
    changed["allergies"].append(
        {"category": "02", "allergen": "Mani", "reaction": "Angioedema"}
    )

    assert compute_background_hash(base) != compute_background_hash(changed)


# ---------------------------------------------------------------------------
# _find_patient_for_sync — global identity resolution
# ---------------------------------------------------------------------------


def _persist(db, *, frontend_id, device_uid, doc="D-1"):
    p = Patient(
        frontend_patient_id=frontend_id,
        organization_id="org-a",
        device_uid=device_uid,
        document_number=doc,
        first_name="Isabella",
        last_name="Martinez",
        full_record_json={"medicalHistory": []},
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_find_for_sync_matches_own_record_by_frontend_id(db_session):
    p = _persist(db_session, frontend_id="APP-1", device_uid="TAG-1")
    # Bracelet replacement: same app id, brand-new tag not yet stored.
    found = _find_patient_for_sync(db_session, "APP-1", "TAG-NEW")
    assert found is not None and found.id == p.id


def test_find_for_sync_resolves_cross_org_by_device_uid(db_session):
    p = _persist(db_session, frontend_id="APP-A", device_uid="TAG-SHARED")
    # A different organization's app id, same physical bracelet.
    found = _find_patient_for_sync(db_session, "APP-B-DIFFERENT", "TAG-SHARED")
    assert found is not None and found.id == p.id


def test_find_for_sync_returns_none_for_new_patient_without_tag(db_session):
    # Neither id nor tag matches, and no device_uid to fall back on.
    assert _find_patient_for_sync(db_session, "APP-UNKNOWN", None) is None


def test_find_for_sync_returns_none_when_tag_unknown(db_session):
    _persist(db_session, frontend_id="APP-A", device_uid="TAG-A")
    assert _find_patient_for_sync(db_session, "APP-UNKNOWN", "TAG-UNKNOWN") is None
