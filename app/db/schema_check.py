"""
Read-only comparison of the database schema against the SQLAlchemy models.

The schema has so far been built with ``scripts/create_tables.py``, which only
creates missing *tables*: a column added to an existing table later (e.g.
``retired_device_uids.device_role``) is never applied, and nothing tells
anyone. Before introducing migrations we need to know what each deployed
database actually looks like; this module answers that at startup, in the
logs, without changing anything.

Audit finding: be-v2-esquema-sin-migraciones.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.db.base import Base

logger = logging.getLogger(__name__)


@dataclass
class SchemaDrift:
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    extra_tables: list[str] = field(default_factory=list)
    extra_columns: dict[str, list[str]] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        """Something the code expects is not in the database."""
        return bool(self.missing_tables or self.missing_columns)


def schema_drift(engine: Engine) -> SchemaDrift:
    """Compare table and column names only; no data is read."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    expected = {table.name: table for table in Base.metadata.sorted_tables}

    drift = SchemaDrift(
        missing_tables=sorted(set(expected) - existing),
        extra_tables=sorted(existing - set(expected)),
    )
    for name in sorted(set(expected) & existing):
        model_columns = {column.name for column in expected[name].columns}
        db_columns = {column["name"] for column in inspector.get_columns(name)}
        if missing := sorted(model_columns - db_columns):
            drift.missing_columns[name] = missing
        if extra := sorted(db_columns - model_columns):
            drift.extra_columns[name] = extra
    return drift


def report_schema_drift(engine: Engine) -> None:
    """
    Log how the database differs from the models. Never raises: this is a
    diagnostic and must not stand between the service and its traffic.
    """
    try:
        drift = schema_drift(engine)
    except Exception as exc:
        logger.error("Schema check could not run: %s", type(exc).__name__)
        return

    if drift.blocking:
        logger.warning(
            "Schema drift: missing tables %s; missing columns %s. Requests that "
            "touch them will fail until the schema is migrated.",
            drift.missing_tables or "none",
            drift.missing_columns or "none",
        )
    else:
        logger.info("Schema check: every table and column the models expect exists.")

    if drift.extra_tables or drift.extra_columns:
        logger.info(
            "Schema check: database objects the models do not define — tables %s; "
            "columns %s.",
            drift.extra_tables or "none",
            drift.extra_columns or "none",
        )


def report_schema_drift_at_startup() -> None:
    from app.db.session import engine

    report_schema_drift(engine)
