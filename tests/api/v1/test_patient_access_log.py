"""
Patient access ledger and the emergency-access uploader.

Audit findings: be-v2-lecturas-de-historias-sin-registro-de-acceso,
be-v2-bitacora-emergencia-actor-declarado-por-cliente (poc10),
be-v2-uid-pulsera-en-url-access-log (POST /scan),
be-v2-scan-falla-si-falta-acudiente-o-fecha.
"""
from copy import deepcopy
from unittest.mock import patch

import pytest

from app.api.deps import get_current_user
from app.core.security import get_password_hash
from app.db.models import (
    EmergencyAccessLog,
    Organization,
    Patient,
    PatientAccessLog,
    User,
    UserRole,
)
from app.main import app
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD

MINOR_UID = MOCK_PATIENT_PAYLOAD["device_uid"]
GUARDIAN_UID = MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]
SEARCH = {
    "document_number": MOCK_PATIENT_PAYLOAD["patientInfo"]["identification"]["documentNumber"],
    "birth_date": MOCK_PATIENT_PAYLOAD["patientInfo"]["dob"],
    "first_name": MOCK_PATIENT_PAYLOAD["patientInfo"]["firstName"],
    "last_name": MOCK_PATIENT_PAYLOAD["patientInfo"]["firstLastName"],
}


def _org(db, name):
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _user(db, org, role, email):
    user = User(
        email=email, full_name="Persona Sintetica",
        hashed_password=get_password_hash("password123"),
        role=role, is_active=True, organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def people(client, db_session):
    org_a = _org(db_session, "Org A")
    org_b = _org(db_session, "Org B")
    hq = _org(db_session, "HQ")
    people = {
        "doc_a": _user(db_session, org_a, UserRole.doctor, "doc@a.org"),
        "doc_b": _user(db_session, org_b, UserRole.doctor, "doc@b.org"),
        "admin_a": _user(db_session, org_a, UserRole.org_admin, "adm@a.org"),
        "sa": _user(db_session, hq, UserRole.superadmin, "sa@hq.org"),
    }
    # Plain ids: the client fixture closes the session after each request.
    return {k: (u.id, u.organization_id) for k, u in people.items()}


def _as(people, who):
    user_id, org_id = people[who]
    user = User(id=user_id, organization_id=org_id, is_active=True,
                role={"doc_a": UserRole.doctor, "doc_b": UserRole.doctor,
                      "admin_a": UserRole.org_admin, "sa": UserRole.superadmin}[who])
    app.dependency_overrides[get_current_user] = lambda: user


def _register(client, people):
    _as(people, "doc_a")
    assert client.post("/api/v1/patients/sync", json=deepcopy(MOCK_PATIENT_PAYLOAD)).status_code == 201


def _log(db_session):
    return db_session.query(PatientAccessLog).order_by(PatientAccessLog.accessed_at).all()


# ---------------------------------------------------------------------------
# Writing the ledger
# ---------------------------------------------------------------------------


def test_sync_scan_and_search_are_recorded(client, db_session, people):
    _register(client, people)
    _as(people, "doc_b")

    scan = client.get(
        f"/api/v1/patients/scan/{MINOR_UID}", headers={"X-Guardian-Device-UID": GUARDIAN_UID}
    )
    search = client.post(
        "/api/v1/patients/search", json={**SEARCH, "access_reason": "Urgencias, sin acudiente"}
    )

    assert scan.status_code == 200 and search.status_code == 200
    rows = _log(db_session)
    assert [(r.channel, r.actor_id, r.guardian_factor, r.reason) for r in rows] == [
        ("sync", people["doc_a"][0], False, None),
        ("scan", people["doc_b"][0], True, None),
        ("search", people["doc_b"][0], False, "Urgencias, sin acudiente"),
    ]
    patient_id = db_session.query(Patient.id).scalar()
    assert {r.patient_id for r in rows} == {patient_id}
    assert rows[1].organization_id == people["doc_b"][1]


def test_post_scan_takes_the_uids_in_the_body(client, db_session, people):
    _register(client, people)

    response = client.post(
        "/api/v1/patients/scan",
        json={"device_uid": MINOR_UID, "guardian_device_uid": GUARDIAN_UID},
    )
    denied = client.post("/api/v1/patients/scan", json={"device_uid": MINOR_UID})

    assert response.status_code == 200
    assert response.json()["patientId"] == MOCK_PATIENT_PAYLOAD["patientId"]
    assert denied.status_code == 403
    assert denied.json()["code"] == "guardian_required"
    assert [r.channel for r in _log(db_session)] == ["sync", "scan"]


def test_adult_scan_is_recorded_without_guardian_factor(client, db_session, people):
    payload = deepcopy(MOCK_PATIENT_PAYLOAD)
    payload["patientInfo"]["dob"] = "1990-01-01"
    _as(people, "doc_a")
    assert client.post("/api/v1/patients/sync", json=payload).status_code == 201

    assert client.get(f"/api/v1/patients/scan/{MINOR_UID}").status_code == 200

    assert _log(db_session)[-1].guardian_factor is False


def test_refused_or_missing_reads_are_not_recorded(client, db_session, people):
    _register(client, people)
    _as(people, "doc_b")

    assert client.get(f"/api/v1/patients/scan/{MINOR_UID}").status_code == 403
    assert client.get("/api/v1/patients/scan/UNKNOWN").status_code == 404
    assert client.post(
        "/api/v1/patients/search", json={**SEARCH, "document_number": "OTRO-999"}
    ).status_code == 404

    assert [r.channel for r in _log(db_session)] == ["sync"]


def test_a_read_that_cannot_be_recorded_is_not_served(client, db_session, people):
    _register(client, people)
    _as(people, "doc_b")

    with patch(
        "app.api.v1.endpoints.patients.record_patient_access",
        side_effect=RuntimeError("database unavailable"),
    ), pytest.raises(RuntimeError):
        client.post("/api/v1/patients/search", json=SEARCH)


def test_scan_without_birth_date_or_guardian_is_not_a_500(client, db_session, people):
    """be-v2-scan-falla-si-falta-acudiente-o-fecha: imported / legacy rows."""
    _register(client, people)
    patient = db_session.query(Patient).one()
    patient.birth_date = None
    record = deepcopy(patient.full_record_json)
    record["guardianInfo"] = None
    patient.full_record_json = record
    db_session.commit()
    _as(people, "doc_a")

    response = client.get(
        f"/api/v1/patients/scan/{MINOR_UID}", headers={"X-Guardian-Device-UID": GUARDIAN_UID}
    )

    # Unknown age is treated as a minor, and no guardian card matches.
    assert response.status_code == 403
    assert response.json()["code"] == "guardian_mismatch"


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------


def _read_log(client, **body):
    return client.post("/api/v1/patients/access-log", json=body)


def test_superadmin_sees_every_access(client, db_session, people):
    _register(client, people)
    _as(people, "doc_b")
    client.post("/api/v1/patients/search", json=SEARCH)

    _as(people, "sa")
    response = _read_log(client, device_uid=MINOR_UID)

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == db_session.query(Patient.id).scalar()
    assert [e["channel"] for e in body["entries"]] == ["search", "sync"]  # newest first
    assert set(body["entries"][0]) == {
        "actor_id", "organization_id", "channel", "guardian_factor", "reason", "accessed_at",
    }


def test_org_admin_sees_only_their_organizations_accesses(client, db_session, people):
    _register(client, people)
    _as(people, "doc_b")
    client.post("/api/v1/patients/search", json=SEARCH)
    patient_id = db_session.query(Patient.id).scalar()

    _as(people, "admin_a")
    response = _read_log(client, patient_id=patient_id, limit=10)

    assert response.status_code == 200
    assert [e["actor_id"] for e in response.json()["entries"]] == [people["doc_a"][0]]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({}, 422),
        ({"patient_id": "x", "device_uid": "y"}, 422),
        ({"patient_id": "no-existe"}, 404),
        ({"device_uid": "UID-NO-EXISTE"}, 404),
        ({"patient_id": "x", "limit": 501}, 422),
    ],
)
def test_access_log_query_validation(client, db_session, people, body, status):
    _as(people, "sa")
    assert _read_log(client, **body).status_code == status


def test_clinicians_cannot_read_the_ledger(client, db_session, people):
    _as(people, "doc_a")
    assert _read_log(client, patient_id="x").status_code == 403


def test_repr_of_an_access_row():
    row = PatientAccessLog(patient_id="p", actor_id="u", channel="scan")
    assert "channel=scan" in repr(row)


# ---------------------------------------------------------------------------
# Emergency access: authenticated uploader (poc10)
# ---------------------------------------------------------------------------


def test_emergency_entries_record_the_authenticated_uploader(client, db_session, people):
    _as(people, "doc_a")
    response = client.post("/api/v1/patients/emergency-access", json={"entries": [{
        "client_event_id": "evt-1",
        "patient_uid": MINOR_UID,
        "patient_name": "Nino Sintetico",
        "user_id": people["doc_b"][0],  # declares someone else
        "reason": "guardian_absent_offline",
        "occurred_at": "1999-01-01T00:00:00",
    }]})

    assert response.status_code == 200
    row = db_session.query(EmergencyAccessLog).one()
    assert row.uploaded_by == people["doc_a"][0]
    assert row.user_id == people["doc_b"][0]  # kept, never trusted instead
    assert row.patient_name is None  # minimisation: the UID identifies the record
