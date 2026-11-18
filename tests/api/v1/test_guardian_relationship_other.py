"""
Guardian relationship "Otro" (requested by the app team, 2026-11-17).

The app's relationship codes are 01 Padres, 02 Hermanos, 03 Tíos, 04 Abuelos
and now 05 Otro, with the text the user wrote in relationshipOther. Records
from before the codes hold free text ("Madre"), which stays valid.
"""
from copy import deepcopy

import pytest

from app.api.deps import get_current_user
from app.db.models import Patient
from app.main import app
from app.schemas.patient import GuardianInfo, PatientFullRecord, fields_not_kept
from app.services.fhir_service import _build_patient_resource
from app.services.patient_service import compute_background_hash
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser

SECOND_GUARDIAN = {"name": "Jaime Díaz", "phone": "3216549743"}


def _payload(guardian=None, guardian2=None):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["guardianInfo"].update(guardian or {})
    if guardian2 is not None:
        payload["guardian2Info"] = {**SECOND_GUARDIAN, **guardian2}
    return payload


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


def _stored_record(db_session):
    db_session.expire_all()
    return db_session.query(Patient).one().full_record_json


def _scan(client):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/scan", json={
        "device_uid": MOCK_PATIENT_PAYLOAD["device_uid"],
        "guardian_device_uid": MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"],
    })


def _guardian(relationship, other=None):
    return GuardianInfo(name="Jaime Díaz", relationship=relationship,
                        relationshipOther=other, phone="3216549743")


# ---------------------------------------------------------------------------
# /sync and /scan
# ---------------------------------------------------------------------------


def test_other_with_its_text_is_stored_and_returned_by_scan(client, db_session):
    payload = _payload(
        {"relationship": "05", "relationshipOther": "Vecino"},
        {"relationship": "05", "relationshipOther": "Madrina"},
    )

    assert _sync(client, payload).status_code == 201

    stored = _stored_record(db_session)
    assert stored["guardianInfo"]["relationshipOther"] == "Vecino"
    assert stored["guardian2Info"]["relationshipOther"] == "Madrina"
    body = _scan(client).json()
    assert (body["guardianInfo"]["relationship"], body["guardianInfo"]["relationshipOther"]) == ("05", "Vecino")
    assert (body["guardian2Info"]["relationship"], body["guardian2Info"]["relationshipOther"]) == ("05", "Madrina")


def test_the_app_payload_keeps_every_field(client, db_session):
    """fields_not_kept logged relationshipOther as dropped before the field existed."""
    payload = _payload({"relationship": "05", "relationshipOther": "Vecino"})
    kept = PatientFullRecord.model_validate(payload).model_dump(mode="json")

    assert fields_not_kept(payload, kept) == []


def test_a_record_stored_before_the_field_returns_null(client, db_session):
    assert _sync(client, _payload()).status_code == 201

    assert _scan(client).json()["guardianInfo"]["relationshipOther"] is None


def test_leaving_other_clears_the_text(client, db_session):
    assert _sync(client, _payload({"relationship": "05", "relationshipOther": "Vecino"})).status_code == 201

    response = _sync(client, _payload({"relationship": "01", "relationshipOther": "Vecino"}))

    assert response.status_code == 201
    stored = _stored_record(db_session)
    assert stored["guardianInfo"]["relationship"] == "01"
    assert stored["guardianInfo"]["relationshipOther"] is None


def test_too_long_a_text_is_rejected(client, db_session):
    response = _sync(client, _payload({"relationship": "05", "relationshipOther": "x" * 101}))

    assert response.status_code == 422, response.text
    assert db_session.query(Patient).count() == 0


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("relationship", "other", "kept"), [
    ("05", "Vecino", "Vecino"),
    ("05", "  Vecino  ", "Vecino"),
    ("05", "x" * 100, "x" * 100),
    ("05", "   ", None),   # blank means no text
    ("05", None, None),
    ("01", "Vecino", None),  # only "Otro" has a text
    ("04", "Vecino", None),
    ("Madre", "Vecino", None),  # free text from before the codes
])
def test_relationship_other_is_kept_only_for_other(relationship, other, kept):
    guardian = _guardian(relationship, other)

    assert guardian.relationship == relationship
    assert guardian.relationshipOther == kept


# ---------------------------------------------------------------------------
# RDA: Patient.contact and the RDA-Paciente hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("relationship", "other", "text"), [
    ("01", None, "Padres"),
    ("02", None, "Hermanos"),
    ("03", None, "Tíos"),
    ("04", None, "Abuelos"),
    ("05", "Vecino", "Vecino"),
    ("05", None, "Otro"),
    ("Madre", None, "Madre"),  # free text goes as is
])
def test_the_rda_contact_names_the_relationship(relationship, other, text):
    payload = _payload(guardian2={"relationship": relationship, "relationshipOther": other})
    patient = PatientFullRecord.model_validate(payload)

    contacts = _build_patient_resource(patient)["contact"]

    assert contacts[1]["relationship"] == [{"text": text}]
    assert contacts[1]["name"]["text"] == "Jaime Díaz"


def test_an_unused_field_does_not_resend_the_rda_paciente():
    """A record dumped with relationshipOther: null hashes like one from before the field."""
    before = deepcopy(MOCK_PATIENT_PAYLOAD)
    after = PatientFullRecord.model_validate(before).model_dump(mode="json")
    assert after["guardianInfo"]["relationshipOther"] is None
    before_dump = deepcopy(after)
    del before_dump["guardianInfo"]["relationshipOther"]

    assert compute_background_hash(before_dump) == compute_background_hash(after)


def test_a_written_relationship_resends_the_rda_paciente():
    """The text goes to Patient.contact, so changing it must reach the RDA."""
    base = PatientFullRecord.model_validate(
        _payload({"relationship": "05", "relationshipOther": "Vecino"})
    ).model_dump(mode="json")
    changed = deepcopy(base)
    changed["guardianInfo"]["relationshipOther"] = "Madrina"

    assert compute_background_hash(base) != compute_background_hash(changed)
