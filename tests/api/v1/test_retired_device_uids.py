"""
Tests for retired-device-UID traceability (lost/damaged bracelet re-labeling).

A bracelet replacement updates the patient's ``device_uid`` in place — no
duplicate patient record is ever created. These tests lock in that:

  * the previous UID is recorded in the append-only ``retired_device_uids``
    ledger with the reason, patient, and acting user,
  * an absent reason defaults to ``"replaced"``,
  * a later scan of the retired tag returns 410 (not a generic 404), while an
    unknown tag still returns 404.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.models import RetiredDeviceUid, UserRole
from app.main import app
from app.schemas.patient import PatientFullRecord
from app.services.fhir import fhir_backend
from app.services.patient_service import (
    create_or_update_patient,
    get_retired_device_uid,
)


class MockUser:
    email = "doctor_test@hwb.org"
    id = "test-user-id"
    role = UserRole.doctor
    organization_id = "org-123"


# Minimal RDA-valid payload — just enough required fields for PatientFullRecord.
BASE_PAYLOAD = {
    "patientId": "RETIRE-UNIT-001",
    "device_uid": "TAG-OLD-001",
    "patientInfo": {
        "identification": {"documentType": "PT", "documentNumber": "VZ-1111111"},
        "firstLastName": "Rodríguez",
        "firstName": "Santiago",
        "dob": "2020-01-01",
        "nationalityCode": "VEN",
        "biologicalSex": "M",
        "address": {"city": "Cúcuta", "state": "Norte de Santander"},
    },
    "guardianInfo": {
        "name": "María Pérez",
        "relationship": "Madre",
        "phone": "+573001234567",
        "device_uid": "GUARDIAN-UID-001",
    },
}


def _override_doctor():
    app.dependency_overrides[get_current_user] = lambda: MockUser()


def _clear_overrides():
    app.dependency_overrides.pop(get_current_user, None)


def _sync(client, payload):
    _override_doctor()
    with patch.object(fhir_backend, "send_bundle") as mock_gcp:
        mock_gcp.return_value = {"status": "success", "google_response": {}}
        return client.post("/api/v1/patients/sync", json=payload)


# ---------------------------------------------------------------------------
# Service-level: retirement is recorded on the replacement path
# ---------------------------------------------------------------------------


def test_replacement_records_retired_uid_with_reason(db_session):
    """Replacing the tag with an explicit reason records it in the ledger."""
    record = PatientFullRecord.model_validate(BASE_PAYLOAD)
    patient, *_ = create_or_update_patient(
        db_session, record, org_id="org-123", actor_user_id="u-1"
    )

    replacement = PatientFullRecord.model_validate(
        {**BASE_PAYLOAD, "device_uid": "TAG-NEW-001", "retiredDeviceReason": "lost"}
    )
    create_or_update_patient(
        db_session, replacement, org_id="org-123", actor_user_id="u-1"
    )

    retired = get_retired_device_uid(db_session, "TAG-OLD-001")
    assert retired is not None
    assert retired.device_uid == "TAG-OLD-001"
    assert retired.patient_id == patient.id
    assert retired.reason == "lost"
    assert retired.retired_by == "u-1"

    # The record was updated in place — one patient, new tag, no duplicate.
    db_session.refresh(patient)
    assert patient.device_uid == "TAG-NEW-001"


def test_replacement_without_reason_defaults_to_replaced(db_session):
    """A tag change with no reason still leaves a trail, marked 'replaced'."""
    create_or_update_patient(
        db_session,
        PatientFullRecord.model_validate(BASE_PAYLOAD),
        org_id="org-123",
    )
    create_or_update_patient(
        db_session,
        PatientFullRecord.model_validate(
            {**BASE_PAYLOAD, "device_uid": "TAG-NEW-002"}
        ),
        org_id="org-123",
    )

    retired = get_retired_device_uid(db_session, "TAG-OLD-001")
    assert retired is not None
    assert retired.reason == "replaced"
    assert retired.retired_by is None


def test_retired_reason_not_persisted_into_clinical_record(db_session):
    """retiredDeviceReason is transport-only and must not leak into stored JSON."""
    create_or_update_patient(
        db_session,
        PatientFullRecord.model_validate(BASE_PAYLOAD),
        org_id="org-123",
    )
    patient, *_ = create_or_update_patient(
        db_session,
        PatientFullRecord.model_validate(
            {**BASE_PAYLOAD, "device_uid": "TAG-NEW-003", "retiredDeviceReason": "damaged"}
        ),
        org_id="org-123",
    )
    assert "retiredDeviceReason" not in (patient.full_record_json or {})


def test_no_retirement_when_tag_unchanged(db_session):
    """Re-syncing the same tag must not create a spurious retirement row."""
    create_or_update_patient(
        db_session,
        PatientFullRecord.model_validate(BASE_PAYLOAD),
        org_id="org-123",
    )
    create_or_update_patient(
        db_session,
        PatientFullRecord.model_validate(BASE_PAYLOAD),
        org_id="org-123",
    )
    assert db_session.query(RetiredDeviceUid).count() == 0


# ---------------------------------------------------------------------------
# Endpoint-level: scanning a retired tag returns 410, unknown returns 404
# ---------------------------------------------------------------------------


def test_scan_retired_tag_returns_410(client: TestClient):
    assert _sync(client, BASE_PAYLOAD).status_code == 201
    replaced = _sync(
        client,
        {**BASE_PAYLOAD, "device_uid": "TAG-NEW-410", "retiredDeviceReason": "lost"},
    )
    assert replaced.status_code == 201

    _override_doctor()
    resp = client.get("/api/v1/patients/scan/TAG-OLD-001")
    _clear_overrides()

    assert resp.status_code == 410
    detail = resp.json()["detail"]
    assert detail["code"] == "device_retired"
    assert detail["reason"] == "lost"


def test_scan_unknown_tag_still_returns_404(client: TestClient):
    _override_doctor()
    resp = client.get("/api/v1/patients/scan/NEVER-SEEN-TAG")
    _clear_overrides()

    assert resp.status_code == 404
