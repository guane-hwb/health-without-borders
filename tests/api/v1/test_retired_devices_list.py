"""
GET /patients/retired-devices: the retired patient and guardian devices, as
hashes, that the app downloads on sync and checks before opening a record
offline.

The hash has to match the app's exactly, so it is pinned by test vectors the
app shares (tests/contract/fixtures/retired_device_hashes.json).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.models import RetiredDeviceUid, UserRole
from app.main import app
from app.schemas.patient import PatientFullRecord
from app.services.fhir import fhir_backend
from app.services.patient_service import create_or_update_patient
from app.services.retired_device_service import (
    RETIRED_DEVICE_HASH_PREFIX,
    SINCE_OVERLAP,
    list_retired_device_hashes,
    normalize_device_uid,
    retired_device_hash,
)
from tests.api.v1.test_retired_device_uids import BASE_PAYLOAD

URL = "/api/v1/patients/retired-devices"

VECTORS = json.loads(
    (
        Path(__file__).parents[2] / "contract" / "fixtures" / "retired_device_hashes.json"
    ).read_text(encoding="utf-8")
)

PATIENT_UID = "04:A1:B2:C3:D4:E5:F6"
GUARDIAN_UID = "04:11:22:33:44:55:66"
PAYLOAD = {
    **BASE_PAYLOAD,
    "device_uid": PATIENT_UID,
    "guardianInfo": {**BASE_PAYLOAD["guardianInfo"], "device_uid": GUARDIAN_UID},
}


def _as(role: UserRole):
    class User:
        email = f"{role.value}@hwb.org"
        id = f"{role.value}-id"
        organization_id = "org-123"

    User.role = role
    app.dependency_overrides[get_current_user] = lambda: User()


def _list(client: TestClient, since=None, role=UserRole.doctor):
    _as(role)
    params = {} if since is None else {"since": since}
    return client.get(URL, params=params)


def _patient(db_session) -> str:
    patient, *_ = create_or_update_patient(
        db_session, PatientFullRecord.model_validate(PAYLOAD), org_id="org-123"
    )
    # The id, not the row: each request closes the shared session.
    return patient.id


def _retire(db_session, patient_id, uid, retired_at=None):
    row = RetiredDeviceUid(
        device_uid=uid, patient_id=patient_id, reason="lost", device_role="patient"
    )
    if retired_at is not None:
        # Stored as UTC: SQLite keeps no zone.
        row.retired_at = retired_at.astimezone(timezone.utc)
    db_session.add(row)
    db_session.commit()


# ---------------------------------------------------------------------------
# The hash the app reproduces
# ---------------------------------------------------------------------------


def test_hash_matches_the_vectors_shared_with_the_app():
    assert RETIRED_DEVICE_HASH_PREFIX == VECTORS["prefix"]
    for vector in VECTORS["vectors"]:
        assert normalize_device_uid(vector["uid"]) == vector["normalized"]
        assert retired_device_hash(vector["uid"]) == vector["hash"]


@pytest.mark.parametrize("uid", [*VECTORS["no_hash"], None])
def test_a_uid_with_no_hex_digit_has_no_hash(uid):
    assert retired_device_hash(uid) is None


def test_a_uid_with_no_hex_digit_is_left_out_of_the_list(db_session):
    patient_id = _patient(db_session)
    _retire(db_session, patient_id, "XYZ")
    _retire(db_session, patient_id, "04:99")

    hashes, _ = list_retired_device_hashes(db_session)

    assert hashes == [retired_device_hash("0499")]


# ---------------------------------------------------------------------------
# What the list holds
# ---------------------------------------------------------------------------


def test_reassigned_devices_are_listed_and_the_new_ones_are_not(client: TestClient):
    _as(UserRole.doctor)
    with patch.object(fhir_backend, "send_bundle") as send:
        send.return_value = {"status": "success", "google_response": {}}
        assert client.post("/api/v1/patients/sync", json=PAYLOAD).status_code == 201
        reassigned = {
            **PAYLOAD,
            "device_uid": "04:0A:0B:0C:0D:0E:0F",
            "guardianInfo": {**PAYLOAD["guardianInfo"], "device_uid": "04:77:88:99"},
            "retiredDeviceReason": "lost",
        }
        assert client.post("/api/v1/patients/sync", json=reassigned).status_code == 201

    response = _list(client)

    assert response.status_code == 200
    body = response.json()
    assert body["uid_hashes"] == sorted(
        [retired_device_hash(PATIENT_UID), retired_device_hash(GUARDIAN_UID)]
    )
    assert retired_device_hash("04:0A:0B:0C:0D:0E:0F") not in body["uid_hashes"]
    assert retired_device_hash("04:77:88:99") not in body["uid_hashes"]
    # The UIDs themselves never travel.
    assert "A1B2" not in response.text.upper()


def test_an_empty_ledger_gives_an_empty_list_and_a_server_time(client, db_session):
    before = datetime.now(timezone.utc)

    body = _list(client).json()

    assert body["uid_hashes"] == []
    assert datetime.fromisoformat(body["server_time"]) >= before


def test_one_device_written_two_ways_or_retired_twice_is_listed_once(client, db_session):
    patient_id = _patient(db_session)
    _retire(db_session, patient_id, "04:a1:b2:c3")
    _retire(db_session, patient_id, "04A1B2C3")
    _retire(db_session, patient_id, "04A1B2C3")

    body = _list(client).json()

    assert body["uid_hashes"] == [retired_device_hash("04A1B2C3")]


# ---------------------------------------------------------------------------
# `since`
# ---------------------------------------------------------------------------


def test_since_returns_only_what_was_retired_after_it(client, db_session):
    patient_id = _patient(db_session)
    now = datetime.now(timezone.utc)
    _retire(db_session, patient_id, "04:01", now - timedelta(hours=2))
    _retire(db_session, patient_id, "04:02", now)

    body = _list(client, since=(now - timedelta(hours=1)).isoformat()).json()

    assert body["uid_hashes"] == [retired_device_hash("04:02")]


def test_since_reads_back_a_few_minutes_before_it(client, db_session):
    """A retirement stamped before `server_time` but committed after it."""
    patient_id = _patient(db_session)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    _retire(db_session, patient_id, "04:03", since - SINCE_OVERLAP / 2)
    _retire(db_session, patient_id, "04:04", since - SINCE_OVERLAP * 2)

    body = _list(client, since=since.isoformat()).json()

    assert body["uid_hashes"] == [retired_device_hash("04:03")]


def test_server_time_passed_back_as_since_misses_nothing(client, db_session):
    patient_id = _patient(db_session)
    _retire(db_session, patient_id, "04:05")
    first = _list(client).json()
    assert first["uid_hashes"] == [retired_device_hash("04:05")]

    # Its transaction started a moment before the first list was served.
    server_time = datetime.fromisoformat(first["server_time"])
    _retire(db_session, patient_id, "04:06", server_time - timedelta(seconds=30))

    body = _list(client, since=first["server_time"]).json()

    assert retired_device_hash("04:06") in body["uid_hashes"]


@pytest.mark.parametrize(
    ("since", "listed"),
    [
        ("2026-10-16T12:05:00", True),  # no offset: UTC
        ("2026-10-16T13:00:00", False),
        ("2026-10-16T07:05:00-05:00", True),  # 12:05 UTC
        ("2026-10-16T08:00:00-05:00", False),  # 13:00 UTC
    ],
)
def test_since_is_compared_in_utc(client, db_session, since, listed):
    patient_id = _patient(db_session)
    _retire(db_session, patient_id, "04:07", datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))

    body = _list(client, since=since).json()

    assert (retired_device_hash("04:07") in body["uid_hashes"]) is listed


def test_since_must_be_a_date_time(client):
    assert _list(client, since="yesterday").status_code == 422


# ---------------------------------------------------------------------------
# Who may read it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.doctor, UserRole.nurse, UserRole.org_admin])
def test_everyone_whose_phone_reads_devices_gets_the_list(client, role):
    assert _list(client, role=role).status_code == 200


def test_superadmin_is_refused(client):
    assert _list(client, role=UserRole.superadmin).status_code == 403


def test_without_a_session_it_is_401(client):
    assert client.get(URL).status_code == 401
