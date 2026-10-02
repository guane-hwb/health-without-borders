"""
The relational copies of the patient record follow it on every sync, and the
fields of the record that are not in the RDA stay out of its hash.

Audit findings (2026-10-02): be-oct26-columnas-relacionales-desalineadas (poc08),
x-oct26-comunidad-etnica-descartada.
"""
from copy import deepcopy

import pytest

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.services.patient_service import compute_background_hash, mirror_columns
from app.services.stats_service import build_overview
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser

GUARDIAN2 = {
    "name": "Carlos Pérez", "relationship": "Padre", "phone": "+573001111111",
    "device_uid": "GUARDIAN2-UID-001",
}
INFO = MOCK_PATIENT_PAYLOAD["patientInfo"]
SEARCH = {
    "document_number": INFO["identification"]["documentNumber"], "birth_date": INFO["dob"],
    "first_name": INFO["firstName"], "last_name": INFO["firstLastName"],
}


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    response = client.post("/api/v1/patients/sync", json=payload)
    assert response.status_code == 201, response.text
    return response


def _payload(nationality="VEN", **top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["patientInfo"]["nationalityCode"] = nationality
    payload.update(deepcopy(top))
    return payload


def _patient(db_session):
    db_session.expire_all()
    return db_session.query(Patient).one()


def test_relational_columns_follow_the_merged_record(client, db_session):
    """poc08: a corrected nationality and a removed guardian lived on in the columns."""
    _sync(client, _payload(guardian2Info=GUARDIAN2))
    _sync(client, _payload("COL", guardian2Info={**GUARDIAN2, "phone": "+573009999999"}))

    patient = _patient(db_session)
    assert (patient.nationality_code, patient.guardian2_phone) == ("COL", "+573009999999")
    assert [(n.code, n.count) for n in build_overview(db_session).nationalities] == [("COL", 1)]

    _sync(client, _payload("COL"))  # the second guardian is removed

    patient = _patient(db_session)
    assert (patient.guardian2_name, patient.guardian2_phone) == (None, None)
    search = client.post("/api/v1/patients/search", json={**SEARCH, "guardian_name": "Carlos"})
    assert search.status_code == 404
    assert client.post(
        "/api/v1/patients/search", json={**SEARCH, "guardian_name": "María"}
    ).status_code == 200


@pytest.mark.parametrize(("sent", "in_record", "column"), [
    ("VEN", "VEN", "VEN"), ("VE", "VE", "VEN"), ("ve", "VE", "VEN"), ("862", "862", "VEN"),
    ("UNK", "UNK", "UNK"), ("OTHER", "UNK", "UNK"),
])
def test_nationality_column_is_alpha3(client, db_session, sent, in_record, column):
    """'VE', 'VEN' and '862' were three countries in the statistics."""
    _sync(client, _payload(sent))

    patient = _patient(db_session)
    assert patient.nationality_code == column
    assert patient.full_record_json["patientInfo"]["nationalityCode"] == in_record


def test_guardian_columns_stay_with_the_guardians_the_merge_kept(client, db_session):
    _sync(client, _payload())
    other_device = _payload(patientId="OTHER-DEVICE-PID")  # resolved by the bracelet
    other_device["guardianInfo"]["name"] = "Otra Persona"

    response = _sync(client, other_device)

    assert response.json()["conflicts"] == ["guardians_not_changed_by_tag_resolved_sync"]
    assert _patient(db_session).guardian_name == "María Pérez"


def test_unknown_or_oversized_values_fall_back_safely():
    columns = mirror_columns({"patientInfo": {"nationalityCode": "XX"}})

    assert columns["nationality_code"] == "UNK"
    assert columns["guardian_name"] is None
    assert mirror_columns({})["nationality_code"] == "UNK"


def test_ethnic_community_does_not_change_the_rda_paciente_hash():
    """It is not in the RDA: adding it must not resend every patient's RDA-Paciente."""
    record = deepcopy(MOCK_PATIENT_PAYLOAD)
    before = compute_background_hash(record)

    record["patientInfo"]["ethnicCommunity"] = None
    assert compute_background_hash(record) == before
    record["patientInfo"]["ethnicCommunity"] = "Comunidad Sintetica"
    assert compute_background_hash(record) == before
    record["patientInfo"]["ethnicity"] = "01"
    assert compute_background_hash(record) != before
    assert compute_background_hash({}) == compute_background_hash({"patientInfo": None})
