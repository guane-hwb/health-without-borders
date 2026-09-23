"""
Write-side validation of /sync and request body limits.

Audit findings: x-v2-nacionalidad-other-impide-alta-en-postgres,
x-v2-documento-acudiente-borrado-por-campos-ignorados,
be-v2-patientid-vacio-fusiona-pacientes (empty ids), be-v2-sin-limites-de-tamano,
be-v2-dependencias-vulnerables-dos-login-sin-auth (login body cap).
"""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.core import request_limits
from app.core.request_limits import (
    MAX_AUTH_BODY_BYTES,
    MAX_BODY_BYTES,
    BodySizeLimitMiddleware,
    body_limit_for,
)
from app.db.models import Patient
from app.main import app
from app.schemas import patient as patient_schema
from app.schemas.emergency_access import EmergencyAccessSyncRequest
from app.schemas.nfc_key_version import NfcKeyVersionSyncRequest
from app.schemas.patient import PatientFullRecord, PatientSyncRecord
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser


def _payload(**patient_info):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["patientInfo"].update(patient_info)
    return payload


def _sync(client, payload):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    return client.post("/api/v1/patients/sync", json=payload)


# ---------------------------------------------------------------------------
# Nationality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["OTHER", "other", " Other ", ""])
def test_other_nationality_is_stored_as_unknown(client, db_session, code):
    response = _sync(client, _payload(nationalityCode=code))

    assert response.status_code == 201, response.text
    patient = db_session.query(Patient).one()
    assert patient.nationality_code == "UNK"
    assert patient.full_record_json["patientInfo"]["nationalityCode"] == "UNK"


def test_nationality_is_normalised_to_upper_case(client, db_session):
    response = _sync(client, _payload(nationalityCode="ven"))

    assert response.status_code == 201, response.text
    assert db_session.query(Patient).one().nationality_code == "VEN"


@pytest.mark.parametrize("code", ["XYZW", "ZZZ", "999"])
def test_unmappable_nationality_is_rejected(client, db_session, code):
    response = _sync(client, _payload(nationalityCode=code))

    assert response.status_code == 422
    assert "nationalityCode must be an ISO 3166-1 code" in response.text
    assert db_session.query(Patient).count() == 0


def test_non_string_nationality_is_left_to_type_validation(client):
    response = _sync(client, _payload(nationalityCode=170))

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Bounded columns and identifiers
# ---------------------------------------------------------------------------


def test_blood_type_longer_than_its_column_is_rejected(client):
    response = _sync(client, _payload(bloodType="O positivo"))

    assert response.status_code == 422
    assert "bloodType is longer than 5" in response.text


@pytest.mark.parametrize("field", ["patientId", "device_uid"])
def test_blank_identifiers_are_rejected(client, db_session, field):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload[field] = "   "

    response = _sync(client, payload)

    assert response.status_code == 422
    assert f"{field} must not be empty" in response.text


def test_empty_patient_id_can_no_longer_merge_two_children(client, db_session):
    """poc11: two children sent with patientId "" used to collapse into one."""
    first = deepcopy(MOCK_PATIENT_PAYLOAD)
    first["patientId"] = ""

    assert _sync(client, first).status_code == 422
    assert db_session.query(Patient).count() == 0


@pytest.mark.parametrize("field", ["patientId", "device_uid"])
def test_overlong_identifiers_are_rejected(client, field):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload[field] = "x" * 129

    response = _sync(client, payload)

    assert response.status_code == 422
    assert f"{field} is longer than 128" in response.text


# ---------------------------------------------------------------------------
# Guardian document aliases (poc24)
# ---------------------------------------------------------------------------


def test_guardian_doc_aliases_are_preserved(client, db_session):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    guardian = payload["guardianInfo"]
    guardian.pop("documentType", None)
    guardian.pop("documentNumber", None)
    guardian["docType"] = "CC"
    guardian["docNumber"] = "ACU-0001"
    payload["guardian2Info"] = {
        "name": "Acudiente Dos",
        "relationship": "Tia",
        "phone": "3000000002",
        "documentNumber": "CANONICO-2",
        "docNumber": "ALIAS-2",
    }

    response = _sync(client, payload)

    assert response.status_code == 201, response.text
    record = db_session.query(Patient).one().full_record_json
    assert record["guardianInfo"]["documentType"] == "CC"
    assert record["guardianInfo"]["documentNumber"] == "ACU-0001"
    assert "docType" not in record["guardianInfo"]
    # When both are sent, the canonical key wins.
    assert record["guardian2Info"]["documentNumber"] == "CANONICO-2"


def test_non_dict_input_skips_normalisation():
    with pytest.raises(ValidationError):
        PatientSyncRecord.model_validate("not a record")


def test_stored_records_are_not_held_to_write_limits():
    """/scan and /search validate stored JSON with PatientFullRecord: a legacy
    record that would now be refused on write must still be readable."""
    legacy = _payload(nationalityCode="OTHER", bloodType="O positivo")

    assert PatientFullRecord.model_validate(legacy).patientInfo.bloodType == "O positivo"


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------


def test_too_many_visits_are_rejected(client, monkeypatch):
    monkeypatch.setitem(patient_schema.MAX_SYNC_LIST_ITEMS, "allergies", 1)
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["allergies"] = [
        {"category": "01", "allergen": "Penicilina"},
        {"category": "01", "allergen": "Amoxicilina"},
    ]

    response = _sync(client, payload)

    assert response.status_code == 422
    assert "allergies has more than 1 items" in response.text


def test_too_many_background_items_are_rejected(client, monkeypatch):
    monkeypatch.setattr(patient_schema, "MAX_BACKGROUND_LIST_ITEMS", 1)
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["backgroundHistory"] = {
        "chronicConditions": [
            {"chronicDescription": "Asma"},
            {"chronicDescription": "Epilepsia"},
        ]
    }

    response = _sync(client, payload)

    assert response.status_code == 422
    assert "backgroundHistory.chronicConditions has more than 1 items" in response.text


def test_background_within_limits_is_accepted(client):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["backgroundHistory"] = {"chronicConditions": [{"chronicDescription": "Asma"}]}

    assert _sync(client, payload).status_code == 201


def _longest_text(payload) -> int:
    return max(len(text) for _, text in patient_schema._walk_strings(payload))


def test_overlong_text_is_rejected(client, monkeypatch):
    limit = _longest_text(MOCK_PATIENT_PAYLOAD)
    monkeypatch.setattr(patient_schema, "MAX_TEXT_LENGTH", limit)

    response = _sync(client, _payload(firstName="N" * (limit + 1)))

    assert response.status_code == 422
    assert f"patientInfo.firstName is longer than {limit}" in response.text


def test_signature_has_its_own_larger_limit(client, monkeypatch):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["guardianInfo"].pop("consent", None)
    text_limit = _longest_text(payload)
    signature_limit = text_limit + 20
    monkeypatch.setattr(patient_schema, "MAX_TEXT_LENGTH", text_limit)
    monkeypatch.setattr(patient_schema, "MAX_SIGNATURE_LENGTH", signature_limit)
    payload["guardianInfo"]["consent"] = {
        "accepted": True,
        "acceptedAt": "2026-09-22T10:00:00",
        "signatureBase64": "A" * signature_limit,
    }

    assert _sync(client, payload).status_code == 201

    payload["guardianInfo"]["consent"]["signatureBase64"] = "A" * (signature_limit + 1)
    response = _sync(client, payload)
    assert response.status_code == 422
    assert f"signatureBase64 is longer than {signature_limit}" in response.text


def _telemetry_entry(i):
    return {
        "device_uid": f"04:{i}",
        "key_version": 0,
        "observed_at": "2026-09-23T10:00:00+00:00",
    }


def _emergency_entry(i):
    return {
        "client_event_id": str(i),
        "patient_uid": "04:AA",
        "reason": "guardian_absent_offline",
        "occurred_at": "2026-09-23T10:00:00",
    }


@pytest.mark.parametrize(
    ("model", "entry"),
    [
        (NfcKeyVersionSyncRequest, _telemetry_entry),
        (EmergencyAccessSyncRequest, _emergency_entry),
    ],
)
def test_batch_uploads_are_capped(model, entry):
    model.model_validate({"entries": [entry(i) for i in range(5000)]})

    with pytest.raises(ValidationError):
        model.model_validate({"entries": [entry(i) for i in range(5001)]})


# ---------------------------------------------------------------------------
# Body size middleware
# ---------------------------------------------------------------------------


def test_body_limit_is_tight_only_for_login():
    assert body_limit_for("/api/v1/login/access-token") == MAX_AUTH_BODY_BYTES
    assert body_limit_for("/api/v1/login/refresh") == MAX_AUTH_BODY_BYTES
    assert body_limit_for("/api/v1/patients/sync") == MAX_BODY_BYTES


def test_oversized_login_form_is_refused_before_parsing(client: TestClient):
    """poc19: 'a;'*N used to take seconds of CPU in python-multipart."""
    response = client.post(
        "/api/v1/login/access-token",
        content=b"a;" * 200_000,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        f"Request body exceeds the {MAX_AUTH_BODY_BYTES} byte limit for this endpoint."
    )


def test_streamed_body_without_length_is_refused(client: TestClient):
    def chunks():
        for _ in range(20):
            yield b"a;" * 1024

    response = client.post(
        "/api/v1/login/access-token",
        content=chunks(),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413


def test_small_login_body_goes_through(client: TestClient):
    response = client.post(
        "/api/v1/login/access-token",
        data={"username": "nadie@org.org", "password": "incorrecta"},
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_limit_error_raised_past_the_app_is_answered_with_413(monkeypatch):
    """An app that reads the stream itself lets the error escape; still a 413."""
    monkeypatch.setattr(request_limits, "MAX_BODY_BYTES", 4)

    async def raw_reader(scope, receive, send):
        await receive()

    sent = []

    async def receive():
        return {"type": "http.request", "body": b"12345", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = BodySizeLimitMiddleware(raw_reader)
    await middleware({"type": "http", "path": "/x", "headers": []}, receive, send)

    assert sent[0]["status"] == 413


def test_validation_errors_do_not_echo_the_payload(client):
    response = _sync(client, _payload(bloodType="O positivo"))

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert set(error) == {"type", "loc", "msg"}
    assert MOCK_PATIENT_PAYLOAD["patientInfo"]["firstName"] not in response.text
