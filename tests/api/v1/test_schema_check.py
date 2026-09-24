"""
Startup schema drift report (audit be-v2-esquema-sin-migraciones, step A.1).
"""
from unittest.mock import patch

from sqlalchemy import Column, MetaData, String, Table, create_engine, text

from app.db import schema_check
from app.db.base import Base


def _engine():
    return create_engine("sqlite://")


def test_matching_schema_reports_no_drift():
    engine = _engine()
    Base.metadata.create_all(engine)

    drift = schema_check.schema_drift(engine)

    assert not drift.blocking
    assert drift.missing_tables == [] and drift.missing_columns == {}
    with patch.object(schema_check.logger, "info") as info, patch.object(
        schema_check.logger, "warning"
    ) as warning:
        schema_check.report_schema_drift(engine)
    warning.assert_not_called()
    assert "every table and column" in info.call_args.args[0]


def test_missing_and_extra_objects_are_reported():
    """poc17: retired_device_uids created before device_role existed."""
    engine = _engine()
    tables = [t for t in Base.metadata.sorted_tables if t.name != "retired_device_uids"]
    Base.metadata.create_all(engine, tables=[t for t in tables if t.name != "nfc_keys"])
    legacy = MetaData()
    Table(
        "retired_device_uids", legacy,
        Column("id", String, primary_key=True), Column("device_uid", String),
        Column("patient_id", String), Column("reason", String),
        Column("retired_at", String), Column("retired_by", String),
        Column("legacy_note", String),
    )
    legacy.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE something_else (id INTEGER)"))

    drift = schema_check.schema_drift(engine)

    assert drift.blocking
    assert drift.missing_tables == ["nfc_keys"]
    assert drift.missing_columns == {"retired_device_uids": ["device_role"]}
    assert drift.extra_tables == ["something_else"]
    assert drift.extra_columns == {"retired_device_uids": ["legacy_note"]}

    with patch.object(schema_check.logger, "info") as info, patch.object(
        schema_check.logger, "warning"
    ) as warning:
        schema_check.report_schema_drift(engine)
    assert warning.call_args.args[1] == ["nfc_keys"]
    assert warning.call_args.args[2] == {"retired_device_uids": ["device_role"]}
    assert info.call_args.args[1] == ["something_else"]


def test_a_failing_check_never_raises():
    with patch.object(schema_check, "schema_drift", side_effect=RuntimeError("down")), \
            patch.object(schema_check.logger, "error") as error:
        schema_check.report_schema_drift(_engine())

    assert error.call_args.args[1] == "RuntimeError"


def test_startup_helper_uses_the_application_engine():
    from app.db.session import engine

    with patch.object(schema_check, "report_schema_drift") as report:
        schema_check.report_schema_drift_at_startup()

    report.assert_called_once_with(engine)
