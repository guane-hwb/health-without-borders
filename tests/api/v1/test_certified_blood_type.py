"""
patientInfo.isBloodTypeCertified (requested by the app team, 2026-11-17).

The blood type can come from an exam or only from what the guardian said.
Decided 2026-11-17: once certified, no sync changes the blood type or
withdraws the certification, not even another certified value. While it is
not certified it changes like any other field.
"""
from copy import deepcopy

import pytest

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.services.patient_service import compute_background_hash
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser

CONFLICT = "blood_type_certified_not_changed"
PENICILINA = {"category": "01", "allergen": "Penicilina"}


def _payload(blood="O+", certified=None, **top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    payload["patientInfo"]["bloodType"] = blood
    if certified is not None:
        payload["patientInfo"]["isBloodTypeCertified"] = certified
    return payload


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


def _stored(db_session):
    db_session.expire_all()
    return db_session.query(Patient).one()


def _stored_blood(db_session):
    patient = _stored(db_session)
    info = patient.full_record_json["patientInfo"]
    return info.get("bloodType"), info.get("isBloodTypeCertified"), patient.blood_type


def _scan(client):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/scan", json={
        "device_uid": MOCK_PATIENT_PAYLOAD["device_uid"],
        "guardian_device_uid": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"],
    })


# ---------------------------------------------------------------------------
# Stored and returned
# ---------------------------------------------------------------------------


def test_a_certified_blood_type_is_stored_and_returned_by_scan(client, db_session):
    assert _sync(client, _payload("O-", certified=True)).status_code == 201

    assert _stored_blood(db_session) == ("O-", True, "O-")
    info = _scan(client).json()["patientInfo"]
    assert (info["bloodType"], info["isBloodTypeCertified"]) == ("O-", True)


def test_a_record_stored_before_the_field_reads_as_not_certified(client, db_session):
    assert _sync(client, _payload()).status_code == 201
    patient = _stored(db_session)
    record = deepcopy(patient.full_record_json)
    record["patientInfo"].pop("isBloodTypeCertified", None)
    patient.full_record_json = record
    db_session.commit()

    assert _scan(client).json()["patientInfo"]["isBloodTypeCertified"] is False


@pytest.mark.parametrize("blood", [None, "", "   "])
def test_without_a_blood_type_nothing_is_certified(client, db_session, blood):
    assert _sync(client, _payload(blood, certified=True)).status_code == 201

    assert _stored_blood(db_session)[1] is False


def test_null_counts_as_not_certified(client, db_session):
    payload = _payload("A+")
    payload["patientInfo"]["isBloodTypeCertified"] = None

    assert _sync(client, payload).status_code == 201
    assert _stored_blood(db_session)[1] is False


def test_a_certified_value_is_written_the_canonical_way(client, db_session):
    assert _sync(client, _payload(" ab+ ", certified=True)).status_code == 201

    assert _stored_blood(db_session) == ("AB+", True, "AB+")


@pytest.mark.parametrize("blood", ["0+", "A", "RH+", "ABO"])
def test_only_a_real_blood_group_can_be_certified(client, db_session, blood):
    response = _sync(client, _payload(blood, certified=True))

    assert response.status_code == 422, response.text
    assert db_session.query(Patient).count() == 0


def test_an_uncertified_value_stays_free_text(client, db_session):
    """Records from before kept what was typed; a 422 would block their sync."""
    assert _sync(client, _payload("0+")).status_code == 201

    assert _stored_blood(db_session)[:2] == ("0+", False)


# ---------------------------------------------------------------------------
# Not certified: edits apply as before
# ---------------------------------------------------------------------------


def test_an_uncertified_blood_type_can_change(client, db_session):
    assert _sync(client, _payload("A+")).status_code == 201

    response = _sync(client, _payload("B+"))

    assert response.json()["conflicts"] == []
    assert _stored_blood(db_session) == ("B+", False, "B+")


def test_an_uncertified_blood_type_can_become_certified(client, db_session):
    assert _sync(client, _payload("A+")).status_code == 201

    response = _sync(client, _payload("O-", certified=True))

    assert response.json()["conflicts"] == []
    assert _stored_blood(db_session) == ("O-", True, "O-")


# ---------------------------------------------------------------------------
# Certified: final
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("blood", "certified"), [
    ("A+", None),    # an app build from before the field
    ("A+", False),
    ("A+", True),    # not even another exam
    ("O-", False),   # the same value, certification withdrawn
])
def test_a_certified_blood_type_does_not_change(client, db_session, app_log, blood, certified):
    assert _sync(client, _payload("O-", certified=True)).status_code == 201

    response = _sync(client, _payload(blood, certified=certified))

    body = response.json()
    assert response.status_code == 201
    assert body["conflicts"] == [CONFLICT]
    assert (body["record"]["patientInfo"]["bloodType"],
            body["record"]["patientInfo"]["isBloodTypeCertified"]) == ("O-", True)
    assert _stored_blood(db_session) == ("O-", True, "O-")
    logged = [r.getMessage() for r in app_log.records if "Certified blood type" in r.getMessage()]
    assert len(logged) == 1
    assert "A+" not in logged[0] and "O-" not in logged[0]


@pytest.mark.parametrize(("blood", "certified"), [
    ("O-", None),    # an app build from before the field: it cannot say
    ("O-", True),
    (" o- ", True),
    (None, None),    # a copy rebuilt from a device may lack it
    ("", None),
])
def test_saying_the_same_or_nothing_is_not_an_edit(client, db_session, blood, certified):
    assert _sync(client, _payload("O-", certified=True)).status_code == 201

    response = _sync(client, _payload(blood, certified=certified))

    assert response.json()["conflicts"] == []
    assert _stored_blood(db_session) == ("O-", True, "O-")


def test_a_stale_copy_does_not_withdraw_the_certification(client, db_session):
    version = _sync(client, _payload("A+")).json()["record_version"]
    # The certifying sync also records an allergy, which the old copy lacks:
    # that is what makes the old copy stale rather than a harmless retry.
    assert _sync(client, _payload("O-", certified=True, baseVersion=version,
                                  allergies=[PENICILINA])).status_code == 201

    response = _sync(client, _payload("A+", certified=False, baseVersion=version))

    assert CONFLICT in response.json()["conflicts"]
    assert "stale_payload_base_version" in response.json()["conflicts"]
    assert _stored_blood(db_session) == ("O-", True, "O-")


def test_a_copy_found_by_the_patient_device_does_not_change_it(client, db_session):
    assert _sync(client, _payload("O-", certified=True)).status_code == 201

    other_device_copy = _payload("B+", certified=False, patientId="OTHER-DEVICE-COPY")
    response = _sync(client, other_device_copy)

    assert response.status_code == 201, response.text
    assert CONFLICT in response.json()["conflicts"]
    assert _stored_blood(db_session) == ("O-", True, "O-")


# ---------------------------------------------------------------------------
# RDA-Paciente
# ---------------------------------------------------------------------------


def test_the_flag_does_not_resend_the_rda_paciente():
    """Not in the RDA: a stored hash stays valid with or without the field."""
    record = deepcopy(MOCK_PATIENT_PAYLOAD)
    certified = deepcopy(record)
    certified["patientInfo"]["isBloodTypeCertified"] = True

    assert compute_background_hash(record) == compute_background_hash(certified)
