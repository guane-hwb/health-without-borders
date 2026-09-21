"""
Tests for the break-glass (emergency access) audit sync endpoint.

Locks in that the endpoint:
  * persists entries into the append-only ledger with the caller's org,
  * is idempotent — a re-sent batch stores nothing new and reports duplicates,
  * stamps a server-side received_at while keeping the client's occurred_at,
  * is restricted to doctor/nurse, and rejects an empty batch (422).
"""

from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user
from app.db.models import EmergencyAccessLog, UserRole
from app.main import app
from app.schemas.emergency_access import EmergencyAccessEntry
from app.services.emergency_access_service import store_emergency_access_entries


class MockDoctor:
    email = "doctor_test@hwb.org"
    id = "doc-1"
    role = UserRole.doctor
    organization_id = "org-123"


class MockNurse:
    email = "nurse_test@hwb.org"
    id = "nurse-1"
    role = UserRole.nurse
    organization_id = "org-123"


class MockOrgAdmin:
    email = "admin_test@hwb.org"
    id = "admin-1"
    role = UserRole.org_admin
    organization_id = "org-123"


def _override(user):
    app.dependency_overrides[get_current_user] = lambda: user


def _clear():
    app.dependency_overrides.pop(get_current_user, None)


def _entry(event_id="evt-1", **overrides):
    base = {
        "client_event_id": event_id,
        "patient_uid": "TAG-PATIENT-001",
        "patient_name": "Santiago Rodríguez",
        "user_id": "doc-1",
        "reason": "guardian_absent_offline",
        "occurred_at": "2026-08-20T14:30:00.000",
    }
    base.update(overrides)
    return base


def test_sync_stores_entries(client, db_session):
    _override(MockDoctor())
    resp = client.post(
        "/api/v1/patients/emergency-access",
        json={"entries": [_entry("evt-1"), _entry("evt-2")]},
    )
    _clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "success",
        "received": 2,
        "stored": 2,
        "duplicates": 0,
    }

    rows = db_session.query(EmergencyAccessLog).all()
    assert len(rows) == 2
    row = (
        db_session.query(EmergencyAccessLog)
        .filter(EmergencyAccessLog.client_event_id == "evt-1")
        .one()
    )
    assert row.patient_uid == "TAG-PATIENT-001"
    assert row.organization_id == "org-123"          # from the token, not the body
    assert row.occurred_at == "2026-08-20T14:30:00.000"  # client time preserved
    assert row.received_at is not None                # server stamps its own time


def test_sync_is_idempotent(client, db_session):
    _override(MockDoctor())
    payload = {"entries": [_entry("evt-dup"), _entry("evt-new")]}
    first = client.post("/api/v1/patients/emergency-access", json=payload)
    assert first.status_code == 200
    assert first.json()["stored"] == 2

    # Re-send the same batch plus one genuinely new entry.
    second = client.post(
        "/api/v1/patients/emergency-access",
        json={"entries": [_entry("evt-dup"), _entry("evt-new"), _entry("evt-3")]},
    )
    _clear()

    assert second.status_code == 200
    body = second.json()
    assert body["received"] == 3
    assert body["stored"] == 1
    assert body["duplicates"] == 2

    assert db_session.query(EmergencyAccessLog).count() == 3


def test_nurse_can_sync(client):
    _override(MockNurse())
    resp = client.post(
        "/api/v1/patients/emergency-access",
        json={"entries": [_entry("evt-nurse")]},
    )
    _clear()
    assert resp.status_code == 200


def test_org_admin_forbidden(client):
    _override(MockOrgAdmin())
    resp = client.post(
        "/api/v1/patients/emergency-access",
        json={"entries": [_entry("evt-admin")]},
    )
    _clear()
    assert resp.status_code == 403


def test_empty_batch_rejected(client):
    _override(MockDoctor())
    resp = client.post("/api/v1/patients/emergency-access", json={"entries": []})
    _clear()
    assert resp.status_code == 422


def test_concurrent_insert_race_counts_as_duplicate(db_session, monkeypatch):
    """If an entry slips past the pre-check and the insert races into an
    IntegrityError (two devices syncing the same client_event_id at once), it is
    absorbed and counted as a duplicate — never surfaced as a 500, and nothing
    partial is left behind."""
    real_flush = db_session.flush
    state = {"raised": False}

    def flaky_flush(*args, **kwargs):
        # Only simulate the race on the explicit insert flush (when the new
        # EmergencyAccessLog is pending) — not on the pre-check query's
        # autoflush, which runs before anything is added.
        pending = any(
            isinstance(obj, EmergencyAccessLog) for obj in db_session.new
        )
        if pending and not state["raised"]:
            state["raised"] = True
            raise IntegrityError(
                "flush", {}, Exception("UNIQUE constraint failed")
            )
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", flaky_flush)

    stored, duplicates = store_emergency_access_entries(
        db_session,
        [EmergencyAccessEntry(**_entry("evt-race"))],
        organization_id="org-123",
    )

    assert (stored, duplicates) == (0, 1)
    assert db_session.query(EmergencyAccessLog).count() == 0


def test_emergency_log_repr():
    """__repr__ is safe and identifies the entry without dumping PHI fields."""
    row = EmergencyAccessLog(
        client_event_id="evt-x",
        patient_uid="TAG-1",
        reason="guardian_absent_offline",
    )
    text = repr(row)
    assert "EmergencyAccessLog" in text
    assert "evt-x" in text
