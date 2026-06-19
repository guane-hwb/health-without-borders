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

from app.services.patient_service import compute_background_hash


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
