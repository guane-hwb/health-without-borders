"""
Contract tests: payloads shaped exactly as the Flutter app serializes them.

The fixtures in ./fixtures follow the app's toJson methods
(health-without-borders-frontend, lib/src/features/nfc/domain/patient_record.dart
at develop f14d662): optional fields are omitted when null rather than sent as
null, the address carries an alpha-3 country, ethnicCommunity is sent twice
(camelCase and snake_case), times carry an offset, the guardian may still carry
the legacy docType/docNumber keys, and so on. A backend change that rejects or
mis-reads any of this breaks installed apps; these tests catch it.

When the app's serialization changes, update the fixtures from its toJson.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.api.deps import get_current_user
from app.db.models import EmergencyAccessLog, Patient
from app.main import app
from tests.api.v1.test_patients import MockNurse, MockUser

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _as(user):
    app.dependency_overrides[get_current_user] = lambda: user()


def _sync(client, payload, user=MockUser):
    _as(user)
    return client.post("/api/v1/patients/sync", json=payload)


def _record(db_session):
    return db_session.query(Patient).one()


@pytest.fixture
def registered(client, db_session):
    response = _sync(client, fixture("register_minor"))
    assert response.status_code == 201, response.text
    return response.json()


def test_registration_as_the_app_sends_it(client, db_session, registered):
    assert registered["status"] == "success"
    assert registered["record_version"] == 1
    assert registered["conflicts"] == []
    patient = _record(db_session)
    assert patient.nationality_code == "UNK"
    stored = patient.full_record_json
    assert stored["guardianInfo"]["documentNumber"] == "SINT-ACU-0001"
    assert stored["guardianInfo"]["consent"]["signatureBase64"]
    # The clinician's background text is kept; the code goes to its own field.
    assert stored["backgroundHistory"]["familyHistory"][0]["conditionDescription"] == "Diabetes"


def test_registration_from_older_builds_with_other_and_doc_aliases(client, db_session):
    payload = fixture("register_minor")
    payload["patientInfo"]["nationalityCode"] = "OTHER"
    guardian = payload["guardianInfo"]
    guardian["docType"] = guardian.pop("documentType")
    guardian["docNumber"] = guardian.pop("documentNumber")
    payload["guardianInfo"]["consent"]["acceptedAt"] = "2026-09-25T10:15:30.123456"  # no offset

    assert _sync(client, payload).status_code == 201
    stored = _record(db_session).full_record_json
    assert stored["patientInfo"]["nationalityCode"] == "UNK"
    assert stored["guardianInfo"]["documentNumber"] == "SINT-ACU-0001"


def test_scan_returns_what_the_app_reads(client, db_session, registered):
    payload = fixture("register_minor")
    _as(MockUser)

    response = client.post("/api/v1/patients/scan", json={
        "device_uid": payload["device_uid"],
        "guardian_device_uid": payload["guardianInfo"]["device_uid"],
    })
    legacy = client.get(
        f"/api/v1/patients/scan/{payload['device_uid']}",
        headers={"X-Guardian-Device-UID": payload["guardianInfo"]["device_uid"]},
    )

    assert response.status_code == legacy.status_code == 200
    body = response.json()
    for key in ("patientId", "device_uid", "patientInfo", "guardianInfo", "allergies",
                "medicalHistory", "vaccinationRecord", "recordVersion"):
        assert key in body
    assert body["patientId"] == payload["patientId"]
    assert body["recordVersion"] == 1


def test_minor_scan_without_card_uses_the_text_the_app_matches(client, db_session, registered):
    """The installed app detects this 403 by finding 'guardian' in the message."""
    _as(MockUser)
    response = client.get(f"/api/v1/patients/scan/{fixture('register_minor')['device_uid']}")

    assert response.status_code == 403
    assert "guardian" in response.json()["detail"].lower()
    assert response.json()["code"] == "guardian_required"


def test_consultation_with_offset_times(client, db_session, registered):
    payload = fixture("register_minor")
    payload["medicalHistory"] = [fixture("consultation")]

    response = _sync(client, payload)

    assert response.status_code == 201
    visits = _record(db_session).full_record_json["medicalHistory"]
    assert [v["encounterIdentifier"] for v in visits] == ["9b1f3c2d-8e4a-4f6b-a1c2-3d4e5f6a7b8c"]
    assert visits[0]["diagnosis"][0]["source"] in {"ai_suggested", "ai_fallback"}


def test_consultation_edited_by_an_older_build_is_not_duplicated(client, db_session, registered):
    payload = fixture("register_minor")
    payload["medicalHistory"] = [fixture("consultation")]
    assert _sync(client, payload).status_code == 201

    edited = deepcopy(fixture("consultation"))
    del edited["encounterIdentifier"]  # older edit screens dropped it
    edited["clinicalEvaluation"]["historyOfCurrentIllness"] = "Nota corregida"
    payload["medicalHistory"] = [edited]
    for _ in range(2):
        assert _sync(client, payload).status_code == 201

    assert len(_record(db_session).full_record_json["medicalHistory"]) == 1


def test_nurse_adds_a_vaccine(client, db_session, registered):
    payload = fixture("register_minor")
    payload["vaccinationRecord"] = [fixture("vaccine")]

    response = _sync(client, payload, user=MockNurse)

    assert response.status_code == 201
    assert _record(db_session).full_record_json["vaccinationRecord"][0]["vaccineCode"] == "03"


def test_emergency_access_upload(client, db_session):
    _as(MockUser)

    response = client.post("/api/v1/patients/emergency-access", json=fixture("emergency_access"))

    assert response.status_code == 200
    assert response.json()["stored"] == 1
    assert db_session.query(EmergencyAccessLog).one().uploaded_by == MockUser.id


def test_nfc_key_version_telemetry(client, db_session):
    _as(MockUser)

    response = client.post("/api/v1/patients/nfc-key-versions", json=fixture("nfc_key_versions"))

    assert response.status_code == 202
    assert response.json() == {"received": 1, "stored": 1}
