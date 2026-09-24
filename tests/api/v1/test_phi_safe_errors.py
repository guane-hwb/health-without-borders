"""
Errors must never carry PHI into logs or responses (audit finding
be-v2-phi-en-logs-por-errores-de-bd-y-manejador-roto), and invalid input must
be answered with a 4xx instead of a 500 (be-v2-entradas-invalidas-responden-500).

Covers:
- PhiSafeFormatter / ScanUidAccessFilter and their wiring in setup_logging
- hide_parameters on the engine
- The global JSON 500 handler
- /sync mapping DataError to 422 and surviving an expired session
- users / organizations mapping FK conflicts to 409 and unknown orgs to 404
- /stats rejecting out-of-range dates
"""
import logging
import sys
from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, IntegrityError
from starlette.requests import Request

from app.api.deps import get_current_user
from app.core.logging import PhiSafeFormatter, ScanUidAccessFilter, setup_logging
from app.core.security import get_password_hash
from app.db.models import Organization, User, UserRole
from app.db.session import engine
from app.main import app, unhandled_exception_handler
from app.schemas.patient import PatientFullRecord
from app.services.patient_service import (
    InvalidPatientDataError,
    create_or_update_patient,
)
from tests.api.v1.test_patients import MOCK_PATIENT_PAYLOAD, MockUser

SYNTHETIC_NAME = "Nino Sintetico"
SYNTHETIC_UID = "04" + "AABB"  # built at runtime so no source line contains it


@pytest.fixture
def app_log(caplog):
    """The "app" logger does not propagate to root, so hook caplog in directly."""
    logger = logging.getLogger("app")
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="app")
    yield caplog
    logger.removeHandler(caplog.handler)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _exc_info_with_chain():
    try:
        try:
            raise ValueError(f"parameters: {SYNTHETIC_NAME}")
        except ValueError as inner:
            raise RuntimeError(f"DETAIL: Key (device_uid)=({SYNTHETIC_UID})") from inner
    except RuntimeError:
        return sys.exc_info()


class TestPhiSafeFormatter:
    def test_traceback_keeps_types_and_frames_but_no_messages(self):
        text = PhiSafeFormatter().formatException(_exc_info_with_chain())

        assert "builtins.ValueError: <message redacted>" in text
        assert "builtins.RuntimeError: <message redacted>" in text
        assert "test_phi_safe_errors.py" in text
        assert SYNTHETIC_NAME not in text
        assert SYNTHETIC_UID not in text
        # Innermost cause comes first, as Python prints chained exceptions.
        assert text.index("ValueError") < text.index("RuntimeError")

    def test_cached_traceback_from_another_formatter_is_discarded(self):
        record = logging.LogRecord(
            "app.x", logging.ERROR, __file__, 1, "boom", None, _exc_info_with_chain()
        )
        record.exc_text = f"stale traceback with {SYNTHETIC_NAME}"

        output = PhiSafeFormatter("%(message)s").format(record)

        assert SYNTHETIC_NAME not in output
        assert "<message redacted>" in output


class TestScanUidAccessFilter:
    def test_masks_uid_in_access_log_arguments(self):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4", "GET", "/api/v1/patients/scan/04AABBCC11?x=1", "1.1", 200),
            None,
        )

        assert ScanUidAccessFilter().filter(record) is True
        assert record.args[2] == "/api/v1/patients/scan/***?x=1"
        assert record.args[4] == 200

    def test_masks_uid_in_preformatted_message(self):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            "GET /api/v1/patients/scan/04AABBCC11 200", None, None,
        )

        ScanUidAccessFilter().filter(record)

        assert record.msg == "GET /api/v1/patients/scan/*** 200"


def test_setup_logging_wires_the_phi_safe_formatter_and_scan_filter():
    setup_logging()

    handler = logging.getLogger("app").handlers[0]
    assert isinstance(handler.formatter, PhiSafeFormatter)
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, ScanUidAccessFilter) for f in access.filters)


def test_engine_hides_statement_parameters():
    assert engine.hide_parameters is True


# ---------------------------------------------------------------------------
# Global 500 handler
# ---------------------------------------------------------------------------


def test_unhandled_error_is_a_json_500_without_details(db_session, monkeypatch):
    def _explode():
        raise RuntimeError(f"secret {SYNTHETIC_NAME}")

    monkeypatch.setattr("app.main.prepare_nfc_keyring_at_startup", lambda: None)
    monkeypatch.setattr("app.main.report_schema_drift_at_startup", lambda: None)
    monkeypatch.setattr("app.main.run_migrations_at_startup", lambda: None)
    app.dependency_overrides[get_current_user] = _explode
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/users/me")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal Server Error"
    assert len(body["error_id"]) == 12
    assert SYNTHETIC_NAME not in response.text


@pytest.mark.anyio
async def test_unhandled_error_handler_without_a_matched_route(app_log):
    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})

    response = await unhandled_exception_handler(request, RuntimeError("x"))

    assert response.status_code == 500
    assert "route=unknown" in app_log.text


# ---------------------------------------------------------------------------
# /sync
# ---------------------------------------------------------------------------


def _raise_data_error(*args, **kwargs):
    raise DataError("INSERT", {}, Exception("value too long for type character varying(3)"))


def test_create_patient_data_error_maps_to_invalid_data(db_session, monkeypatch):
    record = PatientFullRecord.model_validate(MOCK_PATIENT_PAYLOAD)
    monkeypatch.setattr(db_session, "commit", _raise_data_error)

    with pytest.raises(InvalidPatientDataError):
        create_or_update_patient(db_session, record, org_id="org-123")


def test_update_patient_data_error_maps_to_invalid_data(db_session, monkeypatch):
    record = PatientFullRecord.model_validate(MOCK_PATIENT_PAYLOAD)
    create_or_update_patient(db_session, record, org_id="org-123")
    monkeypatch.setattr(db_session, "commit", _raise_data_error)

    with pytest.raises(InvalidPatientDataError):
        create_or_update_patient(db_session, record, org_id="org-123")


def test_sync_invalid_data_returns_422(client: TestClient):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    with patch(
        "app.api.v1.endpoints.patients.create_or_update_patient",
        side_effect=InvalidPatientDataError("too long"),
    ):
        response = client.post("/api/v1/patients/sync", json=deepcopy(MOCK_PATIENT_PAYLOAD))

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "The patient record contains a value the server cannot store."
    )


def test_sync_unexpected_error_log_carries_no_phi(client: TestClient, app_log):
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    with (
        patch(
            "app.api.v1.endpoints.patients.create_or_update_patient",
            side_effect=RuntimeError(f"[parameters: {SYNTHETIC_NAME}]"),
        ),
    ):
        response = client.post("/api/v1/patients/sync", json=deepcopy(MOCK_PATIENT_PAYLOAD))

    assert response.status_code == 500
    formatted = "\n".join(
        PhiSafeFormatter().format(r) for r in app_log.records if r.exc_info
    )
    assert "Critical error during patient sync" in formatted
    assert SYNTHETIC_NAME not in formatted


# ---------------------------------------------------------------------------
# users / organizations
# ---------------------------------------------------------------------------


def _org(db, name):
    org = Organization(name=name, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _user(db, org, role, email):
    user = User(
        email=email,
        full_name="Persona Sintetica",
        hashed_password=get_password_hash("password123"),
        role=role,
        is_active=True,
        organization_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _raise_integrity_error(*args, **kwargs):
    raise IntegrityError("DELETE", {}, Exception("violates foreign key constraint"))


def test_create_user_in_unknown_organization_returns_404(client, db_session):
    sa = _user(db_session, _org(db_session, "HQ"), UserRole.superadmin, "sa@hq.org")
    app.dependency_overrides[get_current_user] = lambda: sa

    response = client.post(
        "/api/v1/users/",
        json={
            "email": "nueva.persona@org.org",
            "full_name": "Persona Sintetica",
            "password": "SecurePassword123!",
            "role": "doctor",
            "organization_id": "no-existe",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Organization not found."


def test_delete_user_with_audit_references_returns_409(client, db_session, monkeypatch):
    org = _org(db_session, "Clinic")
    admin = _user(db_session, org, UserRole.org_admin, "admin@clinic.org")
    doctor = _user(db_session, org, UserRole.doctor, "doc@clinic.org")
    app.dependency_overrides[get_current_user] = lambda: admin
    monkeypatch.setattr(db_session, "commit", _raise_integrity_error)

    response = client.delete(f"/api/v1/users/{doctor.id}")

    assert response.status_code == 409
    assert "Deactivate the account instead" in response.json()["detail"]


def test_delete_organization_with_audit_references_returns_409(
    client, db_session, monkeypatch
):
    sa = _user(db_session, _org(db_session, "HQ"), UserRole.superadmin, "sa@hq.org")
    org = _org(db_session, "Clinic")
    _user(db_session, org, UserRole.doctor, "doc@clinic.org")
    app.dependency_overrides[get_current_user] = lambda: sa
    monkeypatch.setattr(db_session, "commit", _raise_integrity_error)

    response = client.delete(f"/api/v1/organizations/{org.id}")

    assert response.status_code == 409
    assert "Deactivate the organization instead" in response.json()["detail"]


def test_delete_organization_never_removes_a_superadmin(client, db_session):
    sa = _user(db_session, _org(db_session, "HQ"), UserRole.superadmin, "sa@hq.org")
    other_hq = _org(db_session, "Second HQ")
    other_sa = _user(db_session, other_hq, UserRole.superadmin, "sa2@hq.org")
    app.dependency_overrides[get_current_user] = lambda: sa

    response = client.delete(f"/api/v1/organizations/{other_hq.id}")

    assert response.status_code == 409
    assert db_session.query(User).filter(User.id == other_sa.id).first() is not None


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["date_from=0001-01-01", "date_to=9999-12-31", "date_from=1899-12-31"]
)
def test_stats_rejects_out_of_range_dates(client, db_session, query):
    sa = _user(db_session, _org(db_session, "HQ"), UserRole.superadmin, "sa@hq.org")
    app.dependency_overrides[get_current_user] = lambda: sa

    response = client.get(f"/api/v1/stats/overview?{query}")

    assert response.status_code == 422
    assert "between 1900-01-01 and 2100-12-31" in response.json()["detail"]
