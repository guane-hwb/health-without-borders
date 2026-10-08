"""
Which database constraint an ``IntegrityError`` is about.

Audit finding (2026-10-02): be-oct26-integrityerror-mal-clasificado. Any
integrity error on saving a patient was answered as a bracelet conflict, and in
the emergency ledger as a duplicate, so an unrelated failure (a foreign key, a
different unique key) was reported as something it was not, or lost.
"""

from sqlalchemy.exc import IntegrityError


def violates(exc: IntegrityError, constraint: str, sqlite_columns: str) -> bool:
    """
    Whether ``exc`` is a violation of ``constraint``.

    PostgreSQL (psycopg2) names the constraint or unique index that failed.
    SQLite, used by the tests, only names the columns, as in its message
    ``UNIQUE constraint failed: patients.device_uid``; ``sqlite_columns`` is that
    part (``"patients.device_uid"``).
    """
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name is not None:
        return name == constraint
    return str(exc.orig).endswith(f"constraint failed: {sqlite_columns}")
