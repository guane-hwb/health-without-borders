"""
Accounts, sessions and the access ledgers against PostgreSQL, with real
tokens end to end.

Audit PoCs: poc03 (deactivated organization), poc18 (sessions), poc10 (emergency
uploader); findings be-v2-lecturas-de-historias-sin-registro-de-acceso,
be-v2-email-sensible-a-mayusculas.
"""
from copy import deepcopy

from sqlalchemy import text

from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD
from tests.integration.conftest import PASSWORD, login

INFO = MOCK_PATIENT_PAYLOAD["patientInfo"]
SEARCH = {
    "document_number": INFO["identification"]["documentNumber"], "birth_date": INFO["dob"],
    "first_name": INFO["firstName"], "last_name": INFO["firstLastName"],
}
GUARDIAN_UID = MOCK_PATIENT_PAYLOAD["guardianInfo"]["device_uid"]


def _register(api, staff):
    doc = staff["tokens"]["doc_a"]
    response = api.post("/api/v1/patients/sync", headers=doc["headers"], json=deepcopy(MOCK_PATIENT_PAYLOAD))
    assert response.status_code == 201


def _me(api, tokens):
    return api.get("/api/v1/users/me", headers=tokens["headers"])


def _refresh(api, tokens):
    return api.post("/api/v1/login/refresh", json={"refresh_token": tokens["refresh_token"]})


def test_deactivated_organization_is_locked_out(api, staff):
    """poc03: its users kept logging in, refreshing and reading records."""
    sa, doc_b = staff["tokens"]["sa"], staff["tokens"]["doc_b"]
    _register(api, staff)
    url = f"/api/v1/organizations/{staff['ids']['org_b']}"
    assert api.patch(url, headers=sa["headers"], json={"is_active": False}).status_code == 200

    login_attempt = api.post("/api/v1/login/access-token",
                             data={"username": "doc@b.org", "password": PASSWORD})
    assert login_attempt.status_code == 401
    assert login_attempt.json()["code"] == "organization_inactive"
    assert api.post("/api/v1/patients/search", headers=doc_b["headers"], json=SEARCH).status_code == 403

    assert api.patch(url, headers=sa["headers"], json={"is_active": True}).status_code == 200
    assert _refresh(api, doc_b).status_code == 401  # old sessions stay revoked
    assert _me(api, login(api, "doc@b.org")).status_code == 200


def test_session_revocation_end_to_end(api, staff):
    """poc18: reactivation revived a stolen refresh token; reuse went unnoticed."""
    admin, doc = staff["tokens"]["admin_a"], staff["tokens"]["doc_a"]
    user_url = f"/api/v1/users/{staff['ids']['doc_a']}"

    assert api.patch(user_url, headers=admin["headers"], json={"is_active": False}).status_code == 200
    assert api.patch(user_url, headers=admin["headers"], json={"is_active": True}).status_code == 200
    assert _refresh(api, doc).status_code == 401

    fresh = login(api, "DOC@A.ORG")  # e-mail case does not matter
    rotated = _refresh(api, fresh)
    assert rotated.status_code == 200
    assert _refresh(api, fresh).status_code == 401  # reuse: the family dies
    assert _refresh(api, {"refresh_token": rotated.json()["refresh_token"]}).status_code == 401

    again = login(api, "doc@a.org")
    assert api.post(f"{user_url}/revoke-sessions", headers=admin["headers"]).status_code == 204
    assert _me(api, again).status_code == 401


def test_every_read_is_in_the_access_log(api, staff, db):
    _register(api, staff)
    doc_b = staff["tokens"]["doc_b"]

    scan = api.post("/api/v1/patients/scan", headers=doc_b["headers"],
                    json={"device_uid": MOCK_PATIENT_PAYLOAD["device_uid"], "guardian_device_uid": GUARDIAN_UID})
    search = api.post("/api/v1/patients/search", headers=doc_b["headers"],
                      json={**SEARCH, "access_reason": "Urgencias"})
    assert scan.status_code == 200 and search.status_code == 200

    rows = db.execute(text(
        "SELECT channel, actor_id, guardian_factor, reason FROM patient_access_log ORDER BY accessed_at"
    )).all()
    assert [tuple(r) for r in rows] == [
        ("sync", staff["ids"]["doc_a"], False, None),
        ("scan", staff["ids"]["doc_b"], True, None),
        ("search", staff["ids"]["doc_b"], False, "Urgencias"),
    ]

    as_admin_a = api.post("/api/v1/patients/access-log", headers=staff["tokens"]["admin_a"]["headers"],
                          json={"device_uid": MOCK_PATIENT_PAYLOAD["device_uid"]})
    as_superadmin = api.post("/api/v1/patients/access-log", headers=staff["tokens"]["sa"]["headers"],
                             json={"device_uid": MOCK_PATIENT_PAYLOAD["device_uid"]})
    assert [e["channel"] for e in as_admin_a.json()["entries"]] == ["sync"]
    assert [e["channel"] for e in as_superadmin.json()["entries"]] == ["search", "scan", "sync"]


def test_emergency_entries_keep_the_authenticated_uploader(api, staff, db):
    """poc10: the actor was whatever the device declared."""
    doc = staff["tokens"]["doc_a"]
    response = api.post("/api/v1/patients/emergency-access", headers=doc["headers"], json={"entries": [{
        "client_event_id": "evt-1", "patient_uid": "04:AA", "patient_name": "Nino",
        "user_id": staff["ids"]["doc_b"], "reason": "guardian_absent_offline",
        "occurred_at": "2026-09-25T10:00:00",
    }]})

    assert response.status_code == 200
    row = db.execute(text("SELECT uploaded_by, user_id, patient_name FROM emergency_access_log")).one()
    assert tuple(row) == (staff["ids"]["doc_a"], staff["ids"]["doc_b"], None)
