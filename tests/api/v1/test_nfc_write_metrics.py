"""
Tests for NFC write-size telemetry.

Covers storing writes (append-only, role normalisation, the reporting user's
organization and build), the entries that contradict themselves and are
dropped, validation, retention, the summary's arithmetic and window, the org
admin's scope, role gating on both endpoints, and that nothing identifying a
chip or a patient can be stored.

Most tests act as a user through a dependency override, to keep bcrypt out of
every test; TestThroughSignIn runs the same path with real tokens.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import Settings, settings
from app.core.security import get_password_hash
from app.db.models import NfcWriteMetric, Organization, User, UserRole
from app.main import app

SYNC_URL = "/api/v1/patients/nfc-write-metrics"
SUMMARY_URL = "/api/v1/patients/nfc-write-metrics/summary"

BOGOTA = timezone(timedelta(hours=-5))
KOLKATA = timezone(timedelta(hours=5, minutes=30))


def _entry(
    message: int = 230,
    *,
    role: str = "patient",
    payload: int | None = None,
    capacity: int = 492,
    payload_format: int = 1,
    consultations: tuple[int, int] | None = None,
    vaccines: tuple[int, int] | None = None,
    days_ago: float = 0,
    **fields,
) -> dict:
    """One write as the app reports it; ``consultations``/``vaccines`` are (kept, total)."""
    written = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = {
        "device_role": role,
        "payload_format": payload_format,
        "key_version": 0,
        "payload_bytes": max(message - 30, 0) if payload is None else payload,
        "message_bytes": message,
        "chip_capacity": capacity,
        "written_at": written.isoformat(),
    }
    if consultations is not None:
        entry["consultations_kept"], entry["consultations_total"] = consultations
    if vaccines is not None:
        entry["vaccines_kept"], entry["vaccines_total"] = vaccines
    entry.update(fields)
    return entry


@pytest.fixture
def people(db_session):
    """Two clinics and a headquarters, with every role these endpoints tell apart."""
    for org_id in ("org-a", "org-b", "org-hq"):
        db_session.add(Organization(id=org_id, name=f"Clinic {org_id}", is_active=True))
    for user_id, org_id, role in (
        ("doctor-a", "org-a", UserRole.doctor),
        ("nurse-a", "org-a", UserRole.nurse),
        ("admin-a", "org-a", UserRole.org_admin),
        ("doctor-b", "org-b", UserRole.doctor),
        ("admin-b", "org-b", UserRole.org_admin),
        ("super", "org-hq", UserRole.superadmin),
    ):
        db_session.add(
            User(
                id=user_id,
                organization_id=org_id,
                full_name="Metrics Tester",
                email=f"{user_id}@metrics.hwb.org",
                hashed_password="not-used-here",
                role=role,
                is_active=True,
            )
        )
    db_session.commit()


def _as(db, user_id: str) -> None:
    """Act as ``user_id`` on the next requests."""
    app.dependency_overrides[get_current_user] = lambda: db.get(User, user_id)


def _report(client: TestClient, db, user_id: str, *entries, headers=None):
    _as(db, user_id)
    return client.post(SYNC_URL, json={"entries": list(entries)}, headers=headers)


def _summary(client: TestClient, db, user_id: str, **params):
    _as(db, user_id)
    return client.get(SUMMARY_URL, params=params)


def _rows(db) -> list[NfcWriteMetric]:
    db.expire_all()
    return db.query(NfcWriteMetric).order_by(NfcWriteMetric.message_bytes).all()


class TestSyncEndpoint:
    def test_stores_a_batch(self, client, db_session, people):
        resp = _report(
            client, db_session, "doctor-a",
            _entry(230),
            _entry(800, role="guardian", capacity=868, consultations=(3, 5), vaccines=(2, 2)),
        )

        assert resp.status_code == 202
        assert resp.json() == {"received": 2, "stored": 2}

    def test_every_field_is_kept_with_the_reporter_and_its_organization(
        self, client, db_session, people
    ):
        written = datetime(2026, 11, 10, 9, 30, tzinfo=BOGOTA)
        _report(client, db_session, "nurse-a", _entry(
            800, role="guardian", payload=760, capacity=868, payload_format=2,
            consultations=(3, 5), vaccines=(2, 4), key_version=7,
            written_at=written.isoformat(),
        ))

        row = _rows(db_session)[0]
        assert (row.device_role, row.payload_format, row.key_version) == ("guardian", 2, 7)
        assert (row.payload_bytes, row.message_bytes, row.chip_capacity) == (760, 800, 868)
        assert (row.consultations_kept, row.consultations_total) == (3, 5)
        assert (row.vaccines_kept, row.vaccines_total) == (2, 4)
        assert (row.reported_by, row.organization_id) == ("nurse-a", "org-a")
        assert row.reported_at is not None
        # The same instant, kept in UTC (SQLite hands it back without a zone).
        assert row.written_at.replace(tzinfo=timezone.utc) == written

    @pytest.mark.parametrize("counts", [{}, dict.fromkeys(
        ("consultations_kept", "consultations_total", "vaccines_kept", "vaccines_total")
    )], ids=["omitted", "sent-as-null"])
    def test_a_patient_device_has_no_history_counts(self, client, db_session, people, counts):
        resp = _report(client, db_session, "doctor-a", _entry(230, **counts))

        assert resp.json() == {"received": 1, "stored": 1}
        row = _rows(db_session)[0]
        assert row.device_role == "patient"
        assert row.consultations_kept is None and row.consultations_total is None
        assert row.vaccines_kept is None and row.vaccines_total is None

    def test_repeat_writes_are_all_kept(self, client, db_session, people):
        # Append-only: two writes of the same size are two samples.
        for _ in range(3):
            resp = _report(client, db_session, "doctor-a", _entry(230))
            assert resp.json() == {"received": 1, "stored": 1}

        assert len(_rows(db_session)) == 3

    def test_unknown_role_is_stored_as_patient(self, client, db_session, people):
        resp = _report(client, db_session, "doctor-a", _entry(230, role="future-role"))

        # A future device role must never drop a whole batch.
        assert resp.json() == {"received": 1, "stored": 1}
        assert _rows(db_session)[0].device_role == "patient"

    def test_the_role_defaults_to_patient(self, client, db_session, people):
        entry = _entry(230)
        del entry["device_role"]

        assert _report(client, db_session, "doctor-a", entry).status_code == 202
        assert _rows(db_session)[0].device_role == "patient"

    @pytest.mark.parametrize("who", ["doctor-a", "nurse-a", "admin-a"])
    def test_clinical_staff_and_org_admins_can_report(self, client, db_session, people, who):
        # org_admin no longer gets keys, but a device that signed in before
        # keeps its ring until the session ends; its batch must not bounce.
        resp = _report(client, db_session, who, _entry(230))

        assert resp.status_code == 202
        assert resp.json()["stored"] == 1

    def test_superadmin_cannot_report(self, client, db_session, people):
        # A superadmin receives no keys: it can never have written a chip.
        resp = _report(client, db_session, "super", _entry(230))

        assert resp.status_code == 403
        assert resp.json()["detail"] == (
            "Access Denied: Only clinical staff and organization admins "
            "can report NFC write metrics."
        )
        assert _rows(db_session) == []

    def test_requires_authentication(self, client):
        assert client.post(SYNC_URL, json={"entries": [_entry()]}).status_code == 401
        assert client.get(SUMMARY_URL).status_code == 401


class TestAppVersion:
    @pytest.mark.parametrize(
        "headers, stored",
        [
            ({"X-App-Version": "1.2.3+45 (android)"}, "1.2.3+45 (android)"),
            ({}, "none"),
            ({"X-App-Version": "1.2.3; DROP TABLE users"}, "invalid"),
        ],
        ids=["present", "absent", "malformed"],
    )
    def test_the_reporting_build_is_stored(self, client, db_session, people, headers, stored):
        _report(client, db_session, "doctor-a", _entry(230), headers=headers)

        assert _rows(db_session)[0].app_version == stored


class TestValidation:
    def test_empty_batch_rejected(self, client, db_session, people):
        assert _report(client, db_session, "doctor-a").status_code == 422

    def test_batch_over_the_cap_rejected(self, client, db_session, people):
        resp = _report(client, db_session, "doctor-a", *([_entry(230)] * 5001))

        assert resp.status_code == 422
        assert _rows(db_session) == []

    def test_a_missing_size_is_rejected(self, client, db_session, people):
        entry = _entry(230)
        del entry["message_bytes"]

        assert _report(client, db_session, "doctor-a", entry).status_code == 422

    @pytest.mark.parametrize(
        "field, value",
        [
            ("payload_format", 0),
            ("payload_format", 256),
            ("key_version", -1),
            ("key_version", 256),
            ("payload_bytes", -1),
            ("payload_bytes", 65536),
            ("message_bytes", -1),
            ("message_bytes", 65536),
            ("chip_capacity", -1),
            ("chip_capacity", 65536),
            ("consultations_kept", -1),
            ("consultations_total", 1001),
            ("vaccines_kept", 1001),
            ("vaccines_total", -1),
        ],
    )
    def test_out_of_range_values_are_rejected(self, client, db_session, people, field, value):
        resp = _report(client, db_session, "doctor-a", _entry(230, **{field: value}))

        assert resp.status_code == 422
        assert _rows(db_session) == []

    def test_the_largest_values_in_range_are_accepted(self, client, db_session, people):
        resp = _report(client, db_session, "doctor-a", _entry(
            65535, payload=65535, capacity=65535, payload_format=255, key_version=255,
            role="guardian", consultations=(1000, 1000), vaccines=(0, 1000),
        ))

        assert resp.json() == {"received": 1, "stored": 1}


class TestTimestampContract:
    """
    ``written_at`` must carry an offset. A naive value would be read as UTC and
    silently shift every write by the reporting device's timezone — five hours
    in Colombia — and move writes across the edge of the summary window.
    """

    def test_naive_timestamp_is_rejected(self, client, db_session, people):
        resp = _report(client, db_session, "doctor-a", _entry(230, written_at="2026-09-10T10:00:00"))

        assert resp.status_code == 422

    def test_missing_timestamp_is_rejected(self, client, db_session, people):
        entry = _entry(230)
        del entry["written_at"]

        assert _report(client, db_session, "doctor-a", entry).status_code == 422

    @pytest.mark.parametrize("written_at", ["2026-09-10T10:00:00Z", "2026-09-10T05:00:00-05:00"])
    def test_utc_and_offset_timestamps_are_the_same_instant(
        self, client, db_session, people, written_at
    ):
        resp = _report(client, db_session, "doctor-a", _entry(230, written_at=written_at))

        assert resp.status_code == 202
        assert _rows(db_session)[0].written_at == datetime(2026, 9, 10, 10, 0)


class TestInconsistentEntries:
    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param({"payload": 300, "message": 299}, id="message-below-payload"),
            pytest.param({"message": 493, "capacity": 492}, id="message-over-capacity"),
            pytest.param({"message": 1, "payload": 0, "capacity": 0}, id="message-over-zero-capacity"),
            pytest.param({"role": "guardian", "consultations": (4, 3)}, id="consultations-kept-over-total"),
            pytest.param({"role": "guardian", "vaccines": (2, 1)}, id="vaccines-kept-over-total"),
            pytest.param({"role": "guardian", "consultations_kept": 1}, id="consultations-kept-alone"),
            pytest.param({"role": "guardian", "consultations_total": 1}, id="consultations-total-alone"),
            pytest.param({"role": "guardian", "vaccines_kept": 0}, id="vaccines-kept-alone"),
            pytest.param({"role": "guardian", "vaccines_total": 2}, id="vaccines-total-alone"),
        ],
    )
    def test_an_entry_that_contradicts_itself_is_received_not_stored(
        self, client, db_session, people, bad
    ):
        bad = dict(bad)
        resp = _report(
            client, db_session, "doctor-a",
            _entry(230), _entry(bad.pop("message", 230), **bad),
        )

        assert resp.status_code == 202
        assert resp.json() == {"received": 2, "stored": 1}
        assert [r.message_bytes for r in _rows(db_session)] == [230]

    @pytest.mark.parametrize(
        "edge",
        [
            pytest.param({"message": 230, "payload": 230}, id="message-equal-to-payload"),
            pytest.param({"message": 492, "capacity": 492}, id="chip-exactly-full"),
            pytest.param({"message": 0, "payload": 0, "capacity": 0}, id="nothing-on-no-capacity"),
            pytest.param(
                {"role": "guardian", "consultations": (3, 3), "vaccines": (0, 0)},
                id="kept-everything",
            ),
        ],
    )
    def test_edge_cases_that_do_not_contradict_are_stored(self, client, db_session, people, edge):
        edge = dict(edge)
        resp = _report(client, db_session, "doctor-a", _entry(edge.pop("message", 230), **edge))

        assert resp.json() == {"received": 1, "stored": 1}

    def test_the_log_counts_the_drops_and_never_shows_a_value(
        self, client, db_session, people, app_log
    ):
        _report(
            client, db_session, "doctor-a",
            _entry(4321, capacity=4000),
            _entry(4322, capacity=4000),
            _entry(800, role="guardian", capacity=868, consultations=(7, 6)),
            _entry(230),
            headers={"X-App-Version": "1.2.3+45 (android)"},
        )

        warnings = [
            r.getMessage() for r in app_log.records
            if r.levelname == "WARNING" and "NFC write metrics" in r.getMessage()
        ]
        assert warnings == [
            "Dropped 3 NFC write metrics that contradict themselves "
            "(kept_over_total=1, message_over_capacity=2) "
            "reporter_id=doctor-a app_version=1.2.3+45 (android)"
        ]

    def test_a_consistent_batch_logs_no_warning(self, client, db_session, people, app_log):
        _report(client, db_session, "doctor-a", _entry(230))

        assert not [r for r in app_log.records if "NFC write metrics" in r.getMessage()]


class TestRetention:
    def _age(self, db, message: int, days: int) -> None:
        row = db.query(NfcWriteMetric).filter(NfcWriteMetric.message_bytes == message).one()
        row.reported_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()

    def test_metrics_past_the_retention_are_deleted(self, client, db_session, people):
        retention = settings.NFC_WRITE_METRIC_RETENTION_DAYS
        _report(client, db_session, "doctor-a", _entry(111), _entry(222))
        self._age(db_session, 111, retention + 1)
        self._age(db_session, 222, retention - 1)

        _report(client, db_session, "doctor-a", _entry(333))

        assert [r.message_bytes for r in _rows(db_session)] == [222, 333]

    def test_the_retention_runs_on_the_server_clock(self, client, db_session, people):
        # Written long ago, reported now: what ages a row is when it arrived.
        _report(client, db_session, "doctor-a", _entry(111, days_ago=400))

        _report(client, db_session, "doctor-a", _entry(222))

        assert [r.message_bytes for r in _rows(db_session)] == [111, 222]

    def test_the_retention_follows_the_setting(self, client, db_session, people, monkeypatch):
        monkeypatch.setattr(settings, "NFC_WRITE_METRIC_RETENTION_DAYS", 30)
        _report(client, db_session, "doctor-a", _entry(111), _entry(222))
        self._age(db_session, 111, 31)
        self._age(db_session, 222, 29)

        _report(client, db_session, "doctor-a", _entry(333))

        assert [r.message_bytes for r in _rows(db_session)] == [222, 333]

    def test_the_default_retention_is_a_year(self):
        assert Settings.model_fields["NFC_WRITE_METRIC_RETENTION_DAYS"].default == 365


class TestSummaryMath:
    def test_groups_sizes_utilization_and_trimming(self, client, db_session, people):
        guardian = {"role": "guardian", "capacity": 868}
        _report(
            client, db_session, "doctor-a",
            # Guardian copies of format 1 on 868-byte chips.
            _entry(800, consultations=(3, 5), vaccines=(3, 3), **guardian),  # 2 consultations left out
            _entry(860, consultations=(1, 1), vaccines=(2, 5), **guardian),  # 3 vaccines left out
            _entry(500, consultations=(0, 0), vaccines=(0, 0), **guardian),  # an empty record
            _entry(700, **guardian),  # no counts reported
            _entry(820, consultations=(2, 3), vaccines=(1, 2), **guardian),  # both: one trimmed write
            # The same chips with the v2 format.
            _entry(300, payload_format=2, consultations=(3, 3), vaccines=(3, 3), **guardian),
            # A chip that reported no capacity, with nothing written to it.
            _entry(0, payload=0, capacity=0),
        )
        # Ten patient-device writes on 492-byte chips, out of order.
        sizes = [400, 180, 350, 246, 420, 300, 240, 380, 310, 330]
        _report(client, db_session, "nurse-a", *(_entry(size) for size in sizes))

        resp = _summary(client, db_session, "admin-a")

        assert resp.status_code == 200
        assert resp.json() == {
            "window_days": 90,
            "groups": [
                {
                    # sorted sizes 500 700 800 820 860; median index int(0.5 × 4) = 2,
                    # p90 index int(0.9 × 4) = 3; 800 / 868 = 92.17 %
                    "device_role": "guardian", "payload_format": 1, "chip_capacity": 868,
                    "writes": 5,
                    "message_bytes_median": 800,
                    "message_bytes_p90": 820,
                    "message_bytes_max": 860,
                    "utilization_median_pct": 92.2,
                    "trimmed_writes": 3,
                    "consultations_dropped": 3,
                    "vaccines_dropped": 4,
                },
                {
                    # 300 / 868 = 34.56 %
                    "device_role": "guardian", "payload_format": 2, "chip_capacity": 868,
                    "writes": 1,
                    "message_bytes_median": 300,
                    "message_bytes_p90": 300,
                    "message_bytes_max": 300,
                    "utilization_median_pct": 34.6,
                    "trimmed_writes": 0,
                    "consultations_dropped": 0,
                    "vaccines_dropped": 0,
                },
                {
                    # Capacity 0: no utilization rather than a division by zero.
                    "device_role": "patient", "payload_format": 1, "chip_capacity": 0,
                    "writes": 1,
                    "message_bytes_median": 0,
                    "message_bytes_p90": 0,
                    "message_bytes_max": 0,
                    "utilization_median_pct": None,
                    "trimmed_writes": 0,
                    "consultations_dropped": 0,
                    "vaccines_dropped": 0,
                },
                {
                    # sorted 180 240 246 300 310 330 350 380 400 420; median
                    # index int(0.5 × 9) = 4 (the lower middle one: 310, not
                    # 320), p90 index int(0.9 × 9) = 8; 310 / 492 = 63.01 %
                    "device_role": "patient", "payload_format": 1, "chip_capacity": 492,
                    "writes": 10,
                    "message_bytes_median": 310,
                    "message_bytes_p90": 400,
                    "message_bytes_max": 420,
                    "utilization_median_pct": 63.0,
                    "trimmed_writes": 0,
                    "consultations_dropped": 0,
                    "vaccines_dropped": 0,
                },
            ],
        }

    def test_with_two_writes_both_ranks_are_the_smaller_one(self, client, db_session, people):
        # int(0.5 × 1) = int(0.9 × 1) = 0: nearest rank, as specified.
        _report(client, db_session, "doctor-a", _entry(200, capacity=400), _entry(100, capacity=400))

        (group,) = _summary(client, db_session, "admin-a").json()["groups"]

        assert (group["message_bytes_median"], group["message_bytes_p90"]) == (100, 100)
        assert (group["message_bytes_max"], group["utilization_median_pct"]) == (200, 25.0)

    @pytest.mark.parametrize("who", ["admin-a", "super"])
    def test_empty_summary(self, client, db_session, people, who):
        resp = _summary(client, db_session, who)

        assert resp.status_code == 200
        assert resp.json() == {"window_days": 90, "groups": []}

    def test_the_summary_says_nothing_about_who_wrote(self, client, db_session, people):
        _report(client, db_session, "doctor-a", _entry(230))

        (group,) = _summary(client, db_session, "super").json()["groups"]

        assert set(group) == {
            "device_role", "payload_format", "chip_capacity", "writes",
            "message_bytes_median", "message_bytes_p90", "message_bytes_max",
            "utilization_median_pct", "trimmed_writes",
            "consultations_dropped", "vaccines_dropped",
        }


class TestWindow:
    def test_writes_count_by_when_they_were_made(self, client, db_session, people):
        _report(
            client, db_session, "doctor-a",
            _entry(110, days_ago=10), _entry(220, days_ago=100), _entry(330, days_ago=400),
        )

        def window(days=None):
            params = {} if days is None else {"window_days": days}
            body = _summary(client, db_session, "admin-a", **params).json()
            (group,) = body["groups"]
            return body["window_days"], group["writes"], group["message_bytes_max"]

        assert window() == (90, 1, 110)
        assert window(365) == (365, 2, 220)
        # Older than a year by the device's clock, but it arrived today: still kept.
        assert window(730) == (730, 3, 330)

    def test_the_window_compares_instants_not_wall_clocks(self, client, db_session, people):
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        _report(
            client, db_session, "doctor-a",
            # An hour inside the window, from Colombia; an hour outside, from India.
            _entry(111, written_at=(cutoff + timedelta(hours=1)).astimezone(BOGOTA).isoformat()),
            _entry(222, written_at=(cutoff - timedelta(hours=1)).astimezone(KOLKATA).isoformat()),
        )

        (group,) = _summary(client, db_session, "admin-a", window_days=90).json()["groups"]

        assert (group["writes"], group["message_bytes_max"]) == (1, 111)

    @pytest.mark.parametrize(
        "window_days, status",
        [(0, 422), (1, 200), (730, 200), (731, 422), ("ninety", 422)],
    )
    def test_window_bounds(self, client, db_session, people, window_days, status):
        assert _summary(client, db_session, "admin-a", window_days=window_days).status_code == status


class TestScope:
    @pytest.fixture
    def writes(self, client, db_session, people):
        _report(client, db_session, "doctor-a", _entry(100), _entry(200))
        _report(client, db_session, "nurse-a", _entry(300))
        _report(client, db_session, "doctor-b", _entry(600, role="guardian", capacity=868))

    def _groups(self, client, db, who):
        return {
            (g["device_role"], g["chip_capacity"]): g["writes"]
            for g in _summary(client, db, who).json()["groups"]
        }

    def test_an_org_admin_reads_only_its_own_organization(self, client, db_session, writes):
        assert self._groups(client, db_session, "admin-a") == {("patient", 492): 3}
        assert self._groups(client, db_session, "admin-b") == {("guardian", 868): 1}

    def test_a_superadmin_reads_every_organization(self, client, db_session, writes):
        assert self._groups(client, db_session, "super") == {
            ("guardian", 868): 1,
            ("patient", 492): 3,
        }

    def test_an_org_admin_of_an_organization_without_writes_reads_nothing(
        self, client, db_session, people
    ):
        _report(client, db_session, "doctor-a", _entry(100))

        assert self._groups(client, db_session, "admin-b") == {}

    @pytest.mark.parametrize("who", ["doctor-a", "nurse-a"])
    def test_clinical_staff_cannot_read_the_summary(self, client, db_session, people, who):
        resp = _summary(client, db_session, who)

        assert resp.status_code == 403
        assert resp.json()["detail"] == (
            "Access Denied: Only administrators can read NFC write metrics."
        )


class TestNothingIdentifying:
    """No chip UID, no patient, no clinical content can reach the table."""

    def test_fields_outside_the_schema_are_ignored(self, client, db_session, people):
        entry = _entry(800, role="guardian", capacity=868, consultations=(1, 2))
        entry.update({
            "device_uid": "04:A1:B2:C3:D4:E5:F6",
            "patient_id": "patient-123",
            "medicalHistory": [{"diagnosis": "J45"}],
        })

        resp = _report(client, db_session, "doctor-a", entry)

        assert resp.json() == {"received": 1, "stored": 1}
        row = _rows(db_session)[0]
        for name in ("device_uid", "patient_id", "medicalHistory"):
            assert not hasattr(row, name)

    def test_the_table_has_no_column_for_a_chip_or_a_patient(self):
        # A new column here needs the same privacy review as this table had.
        assert {c.name for c in NfcWriteMetric.__table__.columns} == {
            "id", "device_role", "payload_format", "key_version",
            "payload_bytes", "message_bytes", "chip_capacity",
            "consultations_kept", "consultations_total",
            "vaccines_kept", "vaccines_total",
            "written_at", "app_version", "reported_at", "reported_by", "organization_id",
        }


class TestThroughSignIn:
    """The same path with real tokens, as the app and an administrator use it."""

    def _sign_in(self, client, db, user_id: str, org_id: str, role: UserRole) -> dict:
        email = f"{user_id}@metrics.hwb.org"
        db.add(User(
            id=user_id, organization_id=org_id, full_name="Metrics Tester", email=email,
            hashed_password=get_password_hash("SecurePass123"), role=role, is_active=True,
        ))
        db.commit()
        resp = client.post(
            "/api/v1/login/access-token",
            data={"username": email, "password": "SecurePass123"},
        )
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_a_device_reports_and_an_administrator_reads(self, client, db_session):
        db_session.add(Organization(id="org-token", name="Token Clinic", is_active=True))
        db_session.commit()
        doctor = self._sign_in(client, db_session, "doctor-token", "org-token", UserRole.doctor)
        admin = self._sign_in(client, db_session, "admin-token", "org-token", UserRole.org_admin)

        resp = client.post(
            SYNC_URL,
            json={"entries": [_entry(230), _entry(493, capacity=492)]},
            headers={**doctor, "X-App-Version": "1.1.0+10 (android)"},
        )

        assert resp.status_code == 202
        assert resp.json() == {"received": 2, "stored": 1}
        row = _rows(db_session)[0]
        assert (row.reported_by, row.organization_id) == ("doctor-token", "org-token")
        assert row.app_version == "1.1.0+10 (android)"

        assert client.get(SUMMARY_URL, headers=doctor).status_code == 403
        summary = client.get(SUMMARY_URL, headers=admin)
        assert summary.status_code == 200
        assert [g["writes"] for g in summary.json()["groups"]] == [1]
