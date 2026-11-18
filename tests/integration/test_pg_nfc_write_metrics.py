"""
NFC write metrics on PostgreSQL with real tokens: timestamps with an offset
kept as instants, the window, the org admin's scope, retention, and the foreign
key that keeps a reporting user from being deleted.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

SYNC = "/api/v1/patients/nfc-write-metrics"
SUMMARY = "/api/v1/patients/nfc-write-metrics/summary"
BOGOTA = timezone(timedelta(hours=-5))


def _entry(message: int, written_at: datetime, *, role="patient", capacity=492, **counts) -> dict:
    return {
        "device_role": role, "payload_format": 1, "key_version": 0,
        "payload_bytes": message - 30, "message_bytes": message, "chip_capacity": capacity,
        "written_at": written_at.isoformat(), **counts,
    }


def test_write_metrics_round_trip(api, staff, db):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)
    tokens = staff["tokens"]

    response = api.post(SYNC, headers={**tokens["doc_a"]["headers"], "X-App-Version": "1.1.0+10 (android)"},
                        json={"entries": [
                            # An hour inside the 90-day window, written in Colombia.
                            _entry(300, (cutoff + timedelta(hours=1)).astimezone(BOGOTA)),
                            _entry(400, now.astimezone(BOGOTA)),
                            # An hour outside it.
                            _entry(450, cutoff - timedelta(hours=1)),
                            # Larger than the chip: received, not stored.
                            _entry(600, now, capacity=500),
                        ]})
    assert response.status_code == 202, response.text
    assert response.json() == {"received": 4, "stored": 3}
    response = api.post(SYNC, headers=tokens["doc_b"]["headers"], json={"entries": [
        _entry(800, now, role="guardian", capacity=868, consultations_kept=3,
               consultations_total=5, vaccines_kept=3, vaccines_total=3),
    ]})
    assert response.json() == {"received": 1, "stored": 1}

    def groups(who):
        response = api.get(SUMMARY, headers=tokens[who]["headers"])
        assert response.status_code == 200, response.text
        return {
            (g["device_role"], g["chip_capacity"]): (g["writes"], g["message_bytes_max"], g["trimmed_writes"])
            for g in response.json()["groups"]
        }

    assert groups("admin_a") == {("patient", 492): (2, 400, 0)}
    assert groups("sa") == {("patient", 492): (2, 400, 0), ("guardian", 868): (1, 800, 1)}
    assert api.get(SUMMARY, headers=tokens["doc_a"]["headers"]).status_code == 403

    row = db.execute(text(
        "SELECT app_version, written_at, reported_by, organization_id "
        "FROM nfc_write_metrics WHERE message_bytes = 300"
    )).one()
    assert row.app_version == "1.1.0+10 (android)"
    assert row.written_at == cutoff + timedelta(hours=1)
    assert (row.reported_by, row.organization_id) == (staff["ids"]["doc_a"], staff["ids"]["org_a"])

    # Retention, by the server's clock.
    db.execute(text(
        "UPDATE nfc_write_metrics SET reported_at = now() - interval '366 days' "
        "WHERE message_bytes = 450"
    ))
    db.commit()
    assert api.post(SYNC, headers=tokens["doc_a"]["headers"],
                    json={"entries": [_entry(310, now)]}).status_code == 202
    remaining = {m for (m,) in db.execute(text("SELECT message_bytes FROM nfc_write_metrics"))}
    assert remaining == {300, 310, 400, 800}


def test_a_user_who_reported_write_metrics_cannot_be_deleted(api, staff):
    """Like the sightings: the rows reference the user until the retention ends."""
    tokens = staff["tokens"]
    response = api.post(SYNC, headers=tokens["doc_b"]["headers"], json={"entries": [
        _entry(300, datetime.now(timezone.utc)),
    ]})
    assert response.json()["stored"] == 1

    response = api.delete(f"/api/v1/users/{staff['ids']['doc_b']}", headers=tokens["sa"]["headers"])

    assert response.status_code == 409
    assert "Deactivate the account instead" in response.json()["detail"]
