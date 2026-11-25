"""
patientInfo.muac: mid-upper arm circumference in cm (requested 2026-11-25).

Optional, for children of 6 to 59 months; the app shows the risk colour. The
backend only stores it and returns it. App builds from before the field drop
it when they rebuild the record, so a payload without it keeps the stored one.
"""
from copy import deepcopy

import pytest

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.services.patient_service import compute_background_hash
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser

_UNSET = object()


def _payload(muac=_UNSET, **top):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload.update(deepcopy(top))
    if muac is not _UNSET:
        payload["patientInfo"]["muac"] = muac
    return payload


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


def _stored_muac(db_session):
    db_session.expire_all()
    return db_session.query(Patient).one().full_record_json["patientInfo"].get("muac")


def _scan(client):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/scan", json={
        "device_uid": MOCK_PATIENT_PAYLOAD["device_uid"],
        "guardian_device_uid": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"],
    })


def _search(client):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    info = MOCK_PATIENT_PAYLOAD["patientInfo"]
    return client.post("/api/v1/patients/search", json={
        "document_number": info["identification"]["documentNumber"],
        "birth_date": info["dob"],
        "first_name": info["firstName"],
        "last_name": info["firstLastName"],
    })


def test_it_is_stored_and_returned_by_scan_and_search(client, db_session):
    assert _sync(client, _payload(12.3)).status_code == 201

    assert _stored_muac(db_session) == 12.3
    assert _scan(client).json()["patientInfo"]["muac"] == 12.3
    assert _search(client).json()["patientInfo"]["muac"] == 12.3


def test_it_is_optional(client, db_session):
    assert _sync(client, _payload()).status_code == 201

    assert _stored_muac(db_session) is None
    assert _scan(client).json()["patientInfo"]["muac"] is None


def test_a_new_value_replaces_the_stored_one(client, db_session):
    assert _sync(client, _payload(11.2)).status_code == 201

    response = _sync(client, _payload(13.8))

    assert response.json()["conflicts"] == []
    assert _stored_muac(db_session) == 13.8


def test_a_payload_without_the_field_keeps_the_stored_value(client, db_session):
    """An app build from before the field must not erase it."""
    assert _sync(client, _payload(12.3)).status_code == 201

    response = _sync(client, _payload())

    assert response.json()["conflicts"] == []
    assert _stored_muac(db_session) == 12.3
    assert _scan(client).json()["patientInfo"]["muac"] == 12.3


def test_an_explicit_null_clears_it(client, db_session):
    assert _sync(client, _payload(12.3)).status_code == 201

    assert _sync(client, _payload(None)).status_code == 201

    assert _stored_muac(db_session) is None


@pytest.mark.parametrize("muac", [0, -1, 50.1, "doce"])
def test_an_impossible_value_is_rejected(client, db_session, muac):
    response = _sync(client, _payload(muac))

    assert response.status_code == 422, response.text
    assert db_session.query(Patient).count() == 0


def test_it_does_not_resend_the_rda_paciente():
    """Not in the RDA: a stored hash stays valid with or without the field."""
    record = deepcopy(MOCK_PATIENT_PAYLOAD)
    with_null = deepcopy(record)
    with_null["patientInfo"]["muac"] = None
    with_value = deepcopy(record)
    with_value["patientInfo"]["muac"] = 12.3

    before = compute_background_hash(record)
    assert compute_background_hash(with_null) == before
    assert compute_background_hash(with_value) == before
