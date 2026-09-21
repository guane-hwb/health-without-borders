"""
Tests for GET /api/v1/stats/overview and the aggregation service behind it.

Time-dependent behaviour is exercised through ``build_overview(now=...)`` rather
than by patching ``datetime``, so month-boundary assertions stay deterministic.

``created_at`` values are seeded as *naive UTC* datetimes because that is
exactly what SQLite hands back for a ``DateTime(timezone=True)`` column, which
is what the service must cope with in the test environment.
"""

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import get_password_hash
from app.db.models import Organization, Patient, User, UserRole
from app.main import app
from app.services.stats_service import build_overview

# ============================================================================
# Seed helpers
# ============================================================================


def _org(db: Session, name: str) -> Organization:
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _user(db: Session, org: Organization, role: UserRole, email: str) -> User:
    user = User(
        email=email,
        full_name=f"Test {role.value}",
        hashed_password=get_password_hash("secret"),
        role=role,
        is_active=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _patient(
    db: Session,
    org: Organization,
    *,
    created_at: datetime,
    birth_date: date | None = None,
    nationality: str | None = None,
    record: dict | None = None,
) -> Patient:
    patient = Patient(
        frontend_patient_id=str(uuid.uuid4()),
        organization_id=org.id,
        device_uid=str(uuid.uuid4()),
        birth_date=birth_date,
        nationality_code=nationality,
        created_at=created_at,
        full_record_json=record or {},
    )
    db.add(patient)
    db.commit()
    return patient


def _dose(
    day: str,
    *,
    code: str = "141",
    name: str = "Influenza",
    status: str = "completed",
    dose_id: str | None = None,
) -> dict:
    return {
        "vaccinationId": dose_id or str(uuid.uuid4()),
        "date": day,
        "vaccineName": name,
        "vaccineCode": code,
        "dose": 1,
        "administratedBy": "Nurse",
        "administratedAt": "Tent 3",
        "status": status,
    }


def _encounter(started: str, *, encounter_id: str | None = None) -> dict:
    return {
        "encounterIdentifier": encounter_id or str(uuid.uuid4()),
        "startDateTime": started,
        "type": "Consultation",
    }


def _allergy(allergen: str, category: str = "01") -> dict:
    return {"category": category, "allergen": allergen}


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """A naive UTC timestamp, as SQLite returns it."""
    return datetime(year, month, day, hour, minute)


def _now(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 17, 0, tzinfo=timezone.utc)


def _authenticate(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


# ============================================================================
# RBAC
# ============================================================================


@pytest.mark.parametrize("role", [UserRole.doctor, UserRole.nurse])
def test_clinical_roles_are_denied(client: TestClient, db_session: Session, role):
    org = _org(db_session, "Clinic A")
    _authenticate(_user(db_session, org, role, f"{role.value}@a.org"))

    response = client.get("/api/v1/stats/overview")

    assert response.status_code == 403
    assert "Not enough privileges" in response.json()["detail"]


def test_superadmin_aggregates_across_all_organizations(
    client: TestClient, db_session: Session
):
    org_a = _org(db_session, "Org A")
    org_b = _org(db_session, "Org B")
    _patient(db_session, org_a, created_at=_utc(2026, 5, 1))
    _patient(db_session, org_b, created_at=_utc(2026, 5, 2))
    _patient(db_session, org_b, created_at=_utc(2026, 5, 3))

    _authenticate(_user(db_session, org_a, UserRole.superadmin, "boss@hq.org"))

    response = client.get("/api/v1/stats/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == {"organization_id": None, "organization_name": None}
    assert body["totals"]["patients"] == 3


def test_superadmin_can_scope_to_one_organization(
    client: TestClient, db_session: Session
):
    org_a = _org(db_session, "Org A")
    org_b = _org(db_session, "Org B")
    _patient(db_session, org_a, created_at=_utc(2026, 5, 1))
    _patient(db_session, org_b, created_at=_utc(2026, 5, 2))
    _patient(db_session, org_b, created_at=_utc(2026, 5, 3))

    _authenticate(_user(db_session, org_a, UserRole.superadmin, "boss@hq.org"))

    response = client.get(f"/api/v1/stats/overview?organization_id={org_b.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["organization_id"] == org_b.id
    assert body["scope"]["organization_name"] == "Org B"
    assert body["totals"]["patients"] == 2


def test_org_admin_ignores_a_foreign_organization_id(
    client: TestClient, db_session: Session
):
    """An org_admin is pinned to their own tenant regardless of the query."""
    org_a = _org(db_session, "Org A")
    org_b = _org(db_session, "Org B")
    _patient(db_session, org_a, created_at=_utc(2026, 5, 1))
    _patient(db_session, org_b, created_at=_utc(2026, 5, 2))
    _patient(db_session, org_b, created_at=_utc(2026, 5, 3))

    _authenticate(_user(db_session, org_a, UserRole.org_admin, "admin@a.org"))

    response = client.get(f"/api/v1/stats/overview?organization_id={org_b.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["organization_id"] == org_a.id
    assert body["totals"]["patients"] == 1


def test_unknown_organization_returns_404(client: TestClient, db_session: Session):
    org = _org(db_session, "Org A")
    _authenticate(_user(db_session, org, UserRole.superadmin, "boss@hq.org"))

    response = client.get("/api/v1/stats/overview?organization_id=does-not-exist")

    assert response.status_code == 404


def test_inverted_date_window_returns_400(client: TestClient, db_session: Session):
    org = _org(db_session, "Org A")
    _authenticate(_user(db_session, org, UserRole.superadmin, "boss@hq.org"))

    response = client.get(
        "/api/v1/stats/overview?date_from=2026-06-30&date_to=2026-06-01"
    )

    assert response.status_code == 400


def test_empty_organization_returns_zeros(client: TestClient, db_session: Session):
    org = _org(db_session, "Empty Org")
    _authenticate(_user(db_session, org, UserRole.org_admin, "admin@empty.org"))

    response = client.get("/api/v1/stats/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["patients"] == 0
    assert body["totals"]["minors_pct"] == 0.0
    assert body["vaccines"] == []
    assert body["trend"]["patients"]["delta_pct"] is None


# ============================================================================
# Vaccine counting
# ============================================================================


def test_only_completed_doses_are_counted(db_session: Session):
    org = _org(db_session, "Org A")
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 1),
        record={
            "vaccinationRecord": [
                _dose("2026-06-02"),
                _dose("2026-06-02", status="refused"),
                _dose("2026-06-02", status="not_given"),
            ]
        },
    )

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.totals.vaccine_doses == 1


def test_duplicate_dose_ids_are_deduplicated(db_session: Session):
    org = _org(db_session, "Org A")
    repeated = str(uuid.uuid4())
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 1),
        record={
            "vaccinationRecord": [
                _dose("2026-06-02", dose_id=repeated),
                _dose("2026-06-02", dose_id=repeated),
                _dose("2026-06-03"),
            ]
        },
    )

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.totals.vaccine_doses == 2


def test_dose_inside_window_counts_for_patient_registered_earlier(
    db_session: Session,
):
    """
    The core reason vaccines are anchored to the event date.

    A patient enrolled in March who receives a dose during the June brigade must
    appear in June's dose count, while staying out of June's patient cohort.
    """
    org = _org(db_session, "Org A")
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 3, 10),
        record={"vaccinationRecord": [_dose("2026-06-15")]},
    )

    result = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )

    assert result.totals.vaccine_doses == 1
    assert result.totals.patients == 0


def test_vaccines_group_by_code_and_display_the_most_frequent_name(
    db_session: Session,
):
    org = _org(db_session, "Org A")
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 1),
        record={
            "vaccinationRecord": [
                _dose("2026-06-02", code="141", name="Influenza Trivalente"),
                _dose("2026-06-03", code="141", name="Influenza Trivalente"),
                _dose("2026-06-04", code="141", name="Influensa"),
                _dose("2026-06-05", code="", name="Sin código"),
            ]
        },
    )

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    by_code = {v.code: v for v in result.vaccines}
    assert by_code["141"].count == 3
    assert by_code["141"].name == "Influenza Trivalente"
    assert by_code["UNCODED"].count == 1


# ============================================================================
# Cohort metrics
# ============================================================================


def test_minors_percentage_excludes_unknown_birth_dates(db_session: Session):
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 6, 1), birth_date=date(2015, 1, 1))
    _patient(db_session, org, created_at=_utc(2026, 6, 1), birth_date=date(1980, 1, 1))
    _patient(db_session, org, created_at=_utc(2026, 6, 1), birth_date=None)

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.totals.patients == 3
    assert result.totals.patients_with_birth_date == 2
    assert result.totals.minors == 1
    assert result.totals.minors_pct == 50.0


def test_age_is_computed_from_completed_years(db_session: Session):
    """A patient turning 18 tomorrow is still a minor today."""
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 6, 1), birth_date=date(2008, 7, 10))
    _patient(db_session, org, created_at=_utc(2026, 6, 1), birth_date=date(2008, 7, 9))

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.totals.minors == 1


def test_allergies_collapse_case_variants_and_keep_the_canonical_category(
    db_session: Session,
):
    org = _org(db_session, "Org A")
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 1),
        record={"allergies": [_allergy("Maní", "02"), _allergy("maní", "02")]},
    )
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 2),
        record={"allergies": [_allergy("Penicilina", "01")]},
    )

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.totals.allergies == 3
    top = result.allergies[0]
    assert top.allergen == "Maní"
    assert top.category == "02"
    assert top.count == 2


def test_nationalities_bucket_missing_codes_as_unknown(db_session: Session):
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 6, 1), nationality="170")
    _patient(db_session, org, created_at=_utc(2026, 6, 2), nationality="862")
    _patient(db_session, org, created_at=_utc(2026, 6, 3), nationality=None)

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    counts = {n.code: n.count for n in result.nationalities}
    assert counts == {"170": 1, "862": 1, "UNK": 1}


def test_encounters_are_deduplicated_and_anchored_to_their_start_date(
    db_session: Session,
):
    org = _org(db_session, "Org A")
    repeated = str(uuid.uuid4())
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 3, 1),
        record={
            "medicalHistory": [
                _encounter("2026-06-10T09:00:00-05:00", encounter_id=repeated),
                _encounter("2026-06-10T09:00:00-05:00", encounter_id=repeated),
                _encounter("2026-05-10T09:00:00-05:00"),
            ]
        },
    )

    result = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )

    assert result.totals.encounters == 1


# ============================================================================
# Time zone boundaries
# ============================================================================


def test_registration_just_before_local_midnight_stays_in_the_previous_month(
    db_session: Session,
):
    """
    2026-07-01T04:30Z is 2026-06-30T23:30 in Bogota (UTC-5).

    Counting in UTC would move this patient into July and silently distort the
    end of every month.
    """
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 7, 1, 4, 30))

    june = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )
    july = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 31),
        now=_now(2026, 7, 9),
    )

    assert june.totals.patients == 1
    assert july.totals.patients == 0


def test_registration_just_after_local_midnight_falls_in_the_new_month(
    db_session: Session,
):
    """2026-07-01T05:30Z is 2026-07-01T00:30 in Bogota."""
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 7, 1, 5, 30))

    june = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )
    july = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 31),
        now=_now(2026, 7, 9),
    )

    assert june.totals.patients == 0
    assert july.totals.patients == 1


# ============================================================================
# Trend
# ============================================================================


def test_month_trend_compares_equal_elapsed_spans(db_session: Session):
    """
    On 9 July the previous period is 1-9 June, not the whole of June.

    Comparing nine days against thirty would make every metric look like a
    collapse at the start of each month.
    """
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 7, 3))
    _patient(db_session, org, created_at=_utc(2026, 7, 4))
    _patient(db_session, org, created_at=_utc(2026, 6, 5))
    # Outside the truncated previous span: 20 June is after 9 June.
    _patient(db_session, org, created_at=_utc(2026, 6, 20))

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.trend.period == "month"
    assert result.trend.patients.current == 2
    assert result.trend.patients.previous == 1
    assert result.trend.patients.delta_pct == 100.0
    # Totals are unbounded when no window is supplied.
    assert result.totals.patients == 4


def test_month_trend_clamps_the_previous_span_to_a_shorter_month(db_session: Session):
    """On 31 March the previous span is 1-28 February, not 1 Feb - 3 Mar."""
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 2, 27))
    _patient(db_session, org, created_at=_utc(2026, 3, 2))

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 3, 31))

    assert result.trend.patients.previous == 1
    assert result.trend.patients.current == 1


def test_custom_window_trend_compares_the_preceding_equal_length_window(
    db_session: Session,
):
    """June (30 days) is compared against 2-31 May (30 days)."""
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 6, 10))
    _patient(db_session, org, created_at=_utc(2026, 5, 15))
    _patient(db_session, org, created_at=_utc(2026, 5, 20))
    # 1 May falls just outside the preceding window.
    _patient(db_session, org, created_at=_utc(2026, 5, 1))

    result = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )

    assert result.trend.period == "custom"
    assert result.trend.patients.current == 1
    assert result.trend.patients.previous == 2
    assert result.trend.patients.delta_pct == -50.0


def test_delta_is_null_when_the_previous_period_is_empty(db_session: Session):
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 7, 3))

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.trend.patients.current == 1
    assert result.trend.patients.previous == 0
    assert result.trend.patients.delta_pct is None


def test_lone_date_to_falls_back_to_the_monthly_trend(db_session: Session):
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 6, 10))

    result = build_overview(
        db_session,
        organization_id=org.id,
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )

    assert result.trend.period == "month"
    assert result.window.date_from is None
    assert result.totals.patients == 1


def test_naive_reference_time_is_read_as_utc(db_session: Session):
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 7, 3))

    result = build_overview(
        db_session, organization_id=org.id, now=datetime(2026, 7, 9, 17, 0)
    )

    assert result.trend.period == "month"
    assert result.trend.patients.current == 1


# ============================================================================
# Robustness against malformed payloads
# ============================================================================


def test_malformed_record_payload_is_tolerated(db_session: Session):
    """A record that is not a mapping, or whose lists are not lists, is skipped."""
    org = _org(db_session, "Org A")
    _patient(db_session, org, created_at=_utc(2026, 6, 1), record=None)
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 2),
        record={
            "allergies": "not-a-list",
            "vaccinationRecord": [None, 42],
            "medicalHistory": {"nope": True},
        },
    )
    # Blank allergen names carry no information and are dropped.
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 3),
        record={"allergies": [_allergy("   "), _allergy("Látex", "06")]},
    )

    result = build_overview(db_session, organization_id=org.id, now=_now(2026, 7, 9))

    assert result.totals.patients == 3
    assert result.totals.vaccine_doses == 0
    assert result.totals.encounters == 0
    assert result.totals.allergies == 1


def test_unparsable_event_dates_are_excluded_from_a_bounded_window(
    db_session: Session,
):
    """
    An open query reports everything; a bounded one cannot place an unknown date.
    """
    org = _org(db_session, "Org A")
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 1),
        record={"vaccinationRecord": [_dose("not-a-date")]},
    )

    unbounded = build_overview(
        db_session, organization_id=org.id, now=_now(2026, 7, 9)
    )
    bounded = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )

    assert unbounded.totals.vaccine_doses == 1
    assert bounded.totals.vaccine_doses == 0


def test_encounter_timestamps_accept_z_suffix_and_naive_wall_clock(
    db_session: Session,
):
    org = _org(db_session, "Org A")
    _patient(
        db_session,
        org,
        created_at=_utc(2026, 6, 1),
        record={
            "medicalHistory": [
                # 04:30Z on 1 July is still 30 June in Bogota.
                _encounter("2026-07-01T04:30:00Z"),
                # Naive timestamps are the clinician's wall clock, taken as-is.
                _encounter("2026-06-30T23:45:00"),
                # A date-only value still parses.
                _encounter("2026-06-15"),
            ]
        },
    )

    result = build_overview(
        db_session,
        organization_id=org.id,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        now=_now(2026, 7, 9),
    )

    assert result.totals.encounters == 3


def test_timezone_falls_back_to_a_fixed_offset_when_the_tz_database_is_missing(
    monkeypatch,
):
    """Colombia has no daylight saving, so the -5 fallback is exact year-round."""
    from datetime import timedelta

    from app.core import config
    from app.services import stats_service

    monkeypatch.setattr(config.settings, "STATS_TIMEZONE", "Not/AZone")

    resolved = stats_service._resolve_tz()

    assert resolved.utcoffset(None) == timedelta(hours=-5)


def test_server_timestamp_helper_handles_a_missing_value():
    from app.services import stats_service

    assert stats_service._server_date(None, timezone.utc) is None


def test_plain_date_helper_accepts_dates_datetimes_and_junk():
    from app.services import stats_service

    assert stats_service._plain_date(date(2026, 6, 1)) == date(2026, 6, 1)
    assert stats_service._plain_date(datetime(2026, 6, 1, 23, 0)) == date(2026, 6, 1)
    assert stats_service._plain_date("2026-06-01T10:00:00Z") == date(2026, 6, 1)
    assert stats_service._plain_date("nonsense") is None
    assert stats_service._plain_date(None) is None
    assert stats_service._plain_date(42) is None


def test_clinical_date_helper_accepts_datetimes_and_junk():
    from app.services import stats_service

    tz = timezone.utc
    naive = datetime(2026, 6, 1, 23, 0)
    assert stats_service._clinical_date(naive, tz) == date(2026, 6, 1)
    assert stats_service._clinical_date("nonsense", tz) is None
    assert stats_service._clinical_date(42, tz) is None
