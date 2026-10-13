"""
Record versions and baseVersion (audit x-v2-sync-sin-control-de-versiones-pierde-datos-clinicos).

Decision (2026-09-24, option (b)): a payload WITHOUT baseVersion — every app
build before this contract — behaves exactly as before; the conservative merge
applies only when the device says its copy is older.
"""
from copy import deepcopy
from unittest.mock import patch

from pydantic import ValidationError

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.schemas.patient import PatientFullRecord
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, VISIT_1, MockUser

UID = MOCK_PATIENT_PAYLOAD["device_uid"]
GUARDIAN = {"X-Guardian-Device-UID": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]}
PENICILINA = [{"category": "01", "allergen": "Penicilina"}]


def _sync(client, **top):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    return client.post("/api/v1/patients/sync", json=payload)


def _stored(db_session):
    return db_session.query(Patient).one()


def test_versions_start_at_one_and_grow_with_each_sync(client, db_session):
    first = _sync(client)
    second = _sync(client)

    assert first.json()["record_version"] == 1
    assert second.json()["record_version"] == 2
    assert _stored(db_session).record_version == 2


def test_scan_and_search_return_the_version_without_storing_it(client, db_session):
    _sync(client)
    _sync(client, baseVersion=1)

    scan = client.get(f"/api/v1/patients/scan/{UID}", headers=GUARDIAN)
    info = MOCK_PATIENT_PAYLOAD["patientInfo"]
    search = client.post("/api/v1/patients/search", json={
        "document_number": info["identification"]["documentNumber"], "birth_date": info["dob"],
        "first_name": info["firstName"], "last_name": info["firstLastName"],
    })

    assert scan.json()["recordVersion"] == 2
    assert search.json()["recordVersion"] == 2
    stored = _stored(db_session).full_record_json
    assert "recordVersion" not in stored and "baseVersion" not in stored


def test_without_base_version_nothing_changes(client, db_session):
    """Option (b): installed apps can still remove an allergy."""
    _sync(client, allergies=PENICILINA)

    response = _sync(client, allergies=[])

    assert response.json()["conflicts"] == []
    assert _stored(db_session).full_record_json["allergies"] == []


def test_current_base_version_applies_the_change(client, db_session):
    version = _sync(client, allergies=PENICILINA).json()["record_version"]

    response = _sync(client, allergies=[], baseVersion=version)

    assert response.json()["conflicts"] == []
    assert _stored(db_session).full_record_json["allergies"] == []


def test_stale_payload_keeps_allergy_and_current_bracelet(client, db_session):
    """poc05 without a retired UID: device 1 works on version 1 while device 2
    records an allergy and a new guardian card (version 2)."""
    _sync(client)  # version 1, both devices start here
    device_2 = deepcopy(MOCK_PATIENT_PAYLOAD["guardianInfo"])
    device_2["device_uid"] = "GUARDIAN-UID-NUEVA"
    assert _sync(client, allergies=PENICILINA, guardianInfo=device_2, baseVersion=1).status_code == 201

    response = _sync(
        client, allergies=[], medicalHistory=[deepcopy(VISIT_1)], baseVersion=1,
    )

    assert response.status_code == 201
    # Its copy also still carries the guardian card device 2 replaced.
    assert response.json()["conflicts"] == [
        "stale_payload_retired_device_uid", "stale_payload_base_version",
    ]
    assert response.json()["record_version"] == 3
    record = _stored(db_session).full_record_json
    assert [a["allergen"] for a in record["allergies"]] == ["Penicilina"]
    assert record["guardianInfo"]["device_uid"] == "GUARDIAN-UID-NUEVA"
    assert [v["encounterIdentifier"] for v in record["medicalHistory"]] == ["enc-visit-001"]


def test_a_new_patient_ignores_base_version(client, db_session):
    response = _sync(client, baseVersion=7)

    assert response.status_code == 201
    assert response.json()["record_version"] == 1


def test_version_alone_flags_a_stale_copy(client, db_session):
    _sync(client)
    _sync(client, allergies=PENICILINA, baseVersion=1)

    response = _sync(client, allergies=[], baseVersion=1)

    assert response.json()["conflicts"] == ["stale_payload_base_version"]
    assert [a["allergen"] for a in _stored(db_session).full_record_json["allergies"]] == [
        "Penicilina"
    ]


def test_a_retry_of_a_saved_sync_is_not_a_stale_copy(client, db_session):
    """The first attempt was saved but its answer never reached the device (a
    client timeout): the retry carries the same copy on the same base, and
    nothing the stale rule protects differs from what is stored."""
    _sync(client)
    first = _sync(client, allergies=PENICILINA, baseVersion=1)

    retry = _sync(client, allergies=PENICILINA, baseVersion=1)

    assert first.json()["conflicts"] == []
    assert retry.json()["conflicts"] == []
    assert retry.json()["record"] is None
    assert retry.json()["record_version"] == 3


def test_a_stale_copy_gets_the_stored_record_back(client, db_session):
    _sync(client)
    _sync(client, allergies=PENICILINA, baseVersion=1)

    body = _sync(client, allergies=[], baseVersion=1).json()

    assert body["conflicts"] == ["stale_payload_base_version"]
    assert body["record"]["patientId"] == MOCK_PATIENT_PAYLOAD["patientId"]
    assert body["record"]["recordVersion"] == body["record_version"] == 3
    assert [a["allergen"] for a in body["record"]["allergies"]] == ["Penicilina"]


def test_no_record_without_conflicts(client, db_session):
    assert _sync(client).json()["record"] is None
    assert _sync(client, baseVersion=1).json()["record"] is None


def test_no_record_when_the_stored_copy_does_not_validate(client, db_session):
    _sync(client)
    _sync(client, allergies=PENICILINA, baseVersion=1)
    try:
        PatientFullRecord.model_validate({})
    except ValidationError as exc:
        invalid = exc

    with patch(
        "app.api.v1.endpoints.patients.PatientFullRecord.model_validate",
        side_effect=invalid,
    ):
        response = _sync(client, allergies=[], baseVersion=1)

    assert response.status_code == 201
    assert response.json()["conflicts"] == ["stale_payload_base_version"]
    assert response.json()["record"] is None
