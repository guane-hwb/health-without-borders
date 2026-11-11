"""
Two parts of a sync the server keeps as stored, and now says so.

Backend audit 2026-11-04:
- be-nov26-copia-sin-baseversion-borra-alergias (PoC-6): a copy without
  baseVersion erased allergies and background recorded since. Now the lists
  are united and the response says ``missing_base_version``.
- be-nov26-correccion-de-identidad-descartada-en-silencio (PoC-2): a changed
  name or birth date was dropped without a word. Still dropped (identity never
  changes by sync), but the response says ``identity_edit_not_applied`` and
  carries the stored record.
"""
from copy import deepcopy

import pytest

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.services.record_merger import drops_declarative_items
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser

PENICILINA = {"category": "01", "allergen": "Penicilina"}


def _payload(**top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    return payload


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


def _stored(db_session):
    db_session.expire_all()
    return db_session.query(Patient).one()


def _with_info(**fields):
    payload = _payload()
    payload["patientInfo"].update(fields)
    return payload


# ---------------------------------------------------------------------------
# What counts as removing a declarative item
# ---------------------------------------------------------------------------

STORED = {
    "allergies": [PENICILINA],
    "backgroundHistory": {
        "chronicConditions": [{"chronicDescription": "Asma"}],
        "familyHistory": [{"conditionDescription": "Diabetes", "relationship": "Madre"}],
        "medications": [{"medicationName": "Salbutamol"}],
        "personalHistory": "Prematuro",
        "familyHistoryNotes": None,
    },
}


def _without(path):
    record = deepcopy(STORED)
    if path == "allergies":
        record["allergies"] = []
    elif path == "backgroundHistory":
        record["backgroundHistory"] = None
    elif path == "personalHistory":
        record["backgroundHistory"]["personalHistory"] = ""
    else:
        record["backgroundHistory"][path] = []
    return record


@pytest.mark.parametrize("removed", [
    "allergies", "chronicConditions", "familyHistory", "medications", "personalHistory",
    "backgroundHistory",
])
def test_removing_any_declarative_item_counts(removed):
    assert drops_declarative_items(STORED, _without(removed)) is True


def test_adding_reordering_and_editing_an_item_remove_nothing():
    incoming = deepcopy(STORED)
    incoming["allergies"] = [
        {"category": "02", "allergen": "Mani"},
        {"category": "01", "allergen": " penicilina ", "reaction": "Urticaria"},  # same item
    ]
    incoming["backgroundHistory"]["familyHistoryNotes"] = "Nueva nota"

    assert drops_declarative_items(STORED, incoming) is False


def test_a_stored_record_without_background_loses_nothing():
    assert drops_declarative_items({"allergies": []}, {"allergies": []}) is False


# ---------------------------------------------------------------------------
# missing_base_version over /sync
# ---------------------------------------------------------------------------


def test_without_base_version_removed_background_items_stay(client, db_session):
    assert _sync(client, _payload()).status_code == 201
    stored_family = deepcopy(MOCK_PATIENT_PAYLOAD["backgroundHistory"]["familyHistory"])
    assert stored_family  # the fixture has family history to lose
    old_copy = _payload()
    old_copy["backgroundHistory"]["familyHistory"] = []

    response = _sync(client, old_copy)

    assert response.json()["conflicts"] == ["missing_base_version"]
    stored = _stored(db_session).full_record_json["backgroundHistory"]["familyHistory"]
    assert [f["conditionDescription"] for f in stored] == [
        f["conditionDescription"] for f in stored_family
    ]


def test_without_base_version_the_patient_device_still_changes(client, db_session):
    """Only the lists are kept: unlike a stale copy, devices follow the payload."""
    assert _sync(client, _payload(allergies=[PENICILINA])).status_code == 201

    response = _sync(client, _payload(
        allergies=[], device_uid="04:A2:TEST:NUEVA", retiredDeviceReason="lost",
    ))

    assert response.json()["conflicts"] == ["missing_base_version"]
    stored = _stored(db_session)
    assert stored.device_uid == "04:A2:TEST:NUEVA"
    assert [a["allergen"] for a in stored.full_record_json["allergies"]] == ["Penicilina"]


def test_a_stale_copy_reports_only_the_stale_code(client, db_session):
    version = _sync(client, _payload(allergies=[PENICILINA])).json()["record_version"]
    assert _sync(client, _payload(allergies=[PENICILINA], baseVersion=version)).status_code == 201

    response = _sync(client, _payload(allergies=[], baseVersion=version))

    assert response.json()["conflicts"] == ["stale_payload_base_version"]


def test_a_sync_without_base_version_logs_the_app_build(client, db_session, app_log):
    assert _sync(client, _payload()).status_code == 201  # new patient: not logged
    assert "Sync without baseVersion" not in app_log.text

    app.dependency_overrides[get_current_user] = lambda: MockUser()
    client.post("/api/v1/patients/sync", json=_payload(),
                headers={"X-App-Version": "0.9.0+1 (android)"})

    assert "Sync without baseVersion app_version=0.9.0+1 (android)" in app_log.text


# ---------------------------------------------------------------------------
# identity_edit_not_applied over /sync
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change", [
    {"dob": "2006-01-01"},  # PoC-2: a minor who would read as an adult
    {"firstName": "Nicolas"},
    {"firstLastName": "Gomez"},
    {"secondName": "Jose"},
    {"secondLastName": "Lopez"},
    {"biologicalSex": "F"},
    {"identification": {"documentType": "PT", "documentNumber": "VZ-1111111"}},
    {"identification": {"documentType": "PPT", "documentNumber": "VZ-9876543"}},
])
def test_an_identity_edit_is_reported_and_not_applied(client, db_session, app_log, change):
    assert _sync(client, _payload()).status_code == 201

    response = _sync(client, _with_info(**change))

    body = response.json()
    assert response.status_code == 201
    assert body["conflicts"] == ["identity_edit_not_applied"]
    assert body["record"]["patientInfo"]["dob"] == "2020-01-01"
    assert body["record"]["patientInfo"]["firstName"] == "Santiago"
    stored = _stored(db_session)
    assert stored.full_record_json["patientInfo"]["dob"] == "2020-01-01"
    assert stored.full_record_json["patientInfo"]["identification"]["documentNumber"] == "VZ-9876543"
    # Field names only, never values.
    logged = [r.getMessage() for r in app_log.records if "Identity edit" in r.getMessage()]
    assert len(logged) == 1
    assert logged[0].endswith(f"fields={next(iter(change))}")


@pytest.mark.parametrize("same", [
    {"firstName": " SANTIAGO "},
    {"firstLastName": "Rodriguez"},  # without the accent
    {"identification": {"documentType": "PT", "documentNumber": "vz 987.6543"}},
    {"secondName": None, "secondLastName": ""},  # rebuilt from the patient device
])
def test_the_same_identity_written_otherwise_is_not_an_edit(client, db_session, same):
    assert _sync(client, _payload()).status_code == 201

    response = _sync(client, _with_info(**same))

    assert response.json()["conflicts"] == []
    assert _stored(db_session).full_record_json["patientInfo"]["secondName"] == "Andrés"


def test_a_plain_re_sync_reports_nothing(client, db_session):
    assert _sync(client, _payload()).status_code == 201

    response = _sync(client, _payload())

    assert response.json()["conflicts"] == []
    assert response.json()["record"] is None


@pytest.mark.parametrize(("field", "value"), [("biologicalSex", "M"), ("firstName", "Santiago")])
def test_a_field_the_stored_record_lacks_takes_the_payload_without_a_conflict(
    client, db_session, field, value
):
    """Records written by older versions may lack a field; RULE 1 fills it."""
    assert _sync(client, _payload()).status_code == 201
    patient = _stored(db_session)
    record = deepcopy(patient.full_record_json)
    del record["patientInfo"][field]
    patient.full_record_json = record
    db_session.commit()

    response = _sync(client, _payload())

    assert response.json()["conflicts"] == []
    assert _stored(db_session).full_record_json["patientInfo"][field] == value
