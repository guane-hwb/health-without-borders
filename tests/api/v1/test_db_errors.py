"""Which constraint an IntegrityError names (audit be-oct26-integrityerror-mal-clasificado)."""
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from app.db.errors import violates


def _postgres_error(constraint):
    """What psycopg2 attaches: the name of the constraint or unique index."""
    orig = Exception("duplicate key value violates unique constraint")
    orig.diag = SimpleNamespace(constraint_name=constraint)
    return IntegrityError("INSERT", {}, orig)


def test_postgresql_errors_are_matched_by_constraint_name():
    error = _postgres_error("ix_patients_device_uid")

    assert violates(error, "ix_patients_device_uid", "patients.device_uid")
    assert not violates(error, "uq_patient_frontend_id_org", "patients.frontend_patient_id")


def test_sqlite_errors_are_matched_by_columns():
    error = IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: patients.device_uid"))

    assert violates(error, "ix_patients_device_uid", "patients.device_uid")
    assert not violates(
        IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed")),
        "ix_patients_device_uid", "patients.device_uid",
    )
