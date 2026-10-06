"""
Database migrations applied by the service (audit be-v2-esquema-sin-migraciones, A.2).
"""
from unittest.mock import MagicMock, patch

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings
from app.db import migrations
from app.db.base import Base
from app.db.schema_check import schema_drift


def _engine():
    return create_engine("sqlite://")


HEAD = "0005"


def _upgrade(engine, revision: str) -> None:
    from alembic import command

    config = migrations.alembic_config()
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def _database_built_without_alembic(engine) -> None:
    """What scripts/create_tables.py produced before migrations: the 0001
    schema with no alembic_version table (hwb-backend-dev until #55)."""
    _upgrade(engine, "0001")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))


def _version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_empty_database_gets_the_whole_schema():
    engine = _engine()

    migrations.upgrade_to_head(engine)

    assert not schema_drift(engine).blocking
    assert _version(engine) == HEAD


def test_migrations_match_the_models():
    """Changing a model without a migration must fail CI (alembic check)."""
    engine = _engine()
    migrations.upgrade_to_head(engine)

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    assert diff == []


def test_database_built_by_create_tables_is_brought_to_head():
    engine = _engine()
    _database_built_without_alembic(engine)

    migrations.upgrade_to_head(engine)
    migrations.upgrade_to_head(engine)  # a second start changes nothing

    assert _version(engine) == HEAD
    assert not schema_drift(engine).blocking


def test_old_database_gets_the_missing_table_and_column():
    """poc17: a database created before device_role and the NFC key tables."""
    engine = _engine()
    _database_built_without_alembic(engine)
    with engine.begin() as conn:
        for table in ("nfc_key_events", "nfc_keys", "nfc_keyring_state"):
            conn.execute(text(f"DROP TABLE {table}"))
        conn.execute(text("ALTER TABLE retired_device_uids DROP COLUMN device_role"))
        conn.execute(text(
            "INSERT INTO retired_device_uids (id, device_uid, patient_id, reason) "
            "VALUES ('r1', 'UID-OLD', 'p1', 'lost')"
        ))

    migrations.upgrade_to_head(engine)

    assert not schema_drift(engine).blocking
    with engine.connect() as conn:
        role = conn.execute(text("SELECT device_role FROM retired_device_uids")).scalar_one()
    assert role == "patient"  # existing rows get the default
    assert "nfc_keys" in inspect(engine).get_table_names()
    assert _version(engine) == HEAD


def test_0002_downgrades_and_the_baseline_does_not():
    from alembic import command

    engine = _engine()
    migrations.upgrade_to_head(engine)
    config = migrations.alembic_config()

    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "0001")
    assert "patient_access_log" not in inspect(engine).get_table_names()

    with engine.begin() as conn:
        config.attributes["connection"] = conn
        with pytest.raises(NotImplementedError):
            command.downgrade(config, "base")


def test_0005_brings_the_columns_in_line_with_the_records():
    """be-oct26-columnas-relacionales-desalineadas: columns set only at creation."""
    import json

    engine = _engine()
    _upgrade(engine, "0004")
    rows = {
        # nationality corrected, second guardian removed after creation
        "p1": ({"patientInfo": {"nationalityCode": "CO", "bloodType": "O+"},
                "guardianInfo": {"name": "Acudiente", "phone": "300"}},
               {"nationality_code": "VEN", "guardian2_name": "Retirado", "guardian2_phone": "301"}),
        # a blood type from before the length check does not fit the column
        "p2": ({"patientInfo": {"nationalityCode": "862", "bloodType": "O positivo"},
                "guardianInfo": {"name": "Otra"}},
               {"nationality_code": "VE", "blood_type": "O+"}),
    }
    with engine.begin() as conn:
        for pid, (record, columns) in rows.items():
            values = {"id": pid, "frontend_patient_id": pid, "organization_id": "org",
                      "device_uid": f"UID-{pid}",
                      "full_record_json": json.dumps(record), **columns}
            conn.execute(text(
                f"INSERT INTO patients ({', '.join(values)}) "
                f"VALUES ({', '.join(':' + name for name in values)})"
            ), values)

    _upgrade(engine, "0005")

    with engine.connect() as conn:
        stored = {row.id: row for row in conn.execute(text(
            "SELECT id, nationality_code, blood_type, guardian_name, guardian_phone, "
            "guardian2_name, guardian2_phone FROM patients"
        ))}
    assert tuple(stored["p1"])[1:] == ("COL", "O+", "Acudiente", "300", None, None)
    assert tuple(stored["p2"])[1:] == ("VEN", "O+", "Otra", None, None, None)


def test_postgresql_takes_the_advisory_lock_first():
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = connection

    with patch.object(migrations.command, "upgrade") as upgrade:
        migrations.upgrade_to_head(engine)

    statement, params = connection.execute.call_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {"key": migrations.MIGRATION_LOCK_KEY}
    assert upgrade.call_args.args[1] == "head"


class TestStartupHook:
    def test_disabled_by_setting(self, monkeypatch):
        monkeypatch.setattr(settings, "RUN_MIGRATIONS_ON_STARTUP", False)
        with patch.object(migrations, "upgrade_to_head") as upgrade:
            migrations.run_migrations_at_startup()
        upgrade.assert_not_called()

    def test_runs_against_the_application_engine(self, monkeypatch):
        from app.db.session import engine

        monkeypatch.setattr(settings, "RUN_MIGRATIONS_ON_STARTUP", True)
        with patch.object(migrations, "upgrade_to_head") as upgrade:
            migrations.run_migrations_at_startup()
        upgrade.assert_called_once_with(engine)

    def test_a_failed_migration_stops_startup_and_logs_only_the_type(self, monkeypatch):
        monkeypatch.setattr(settings, "RUN_MIGRATIONS_ON_STARTUP", True)
        with patch.object(
            migrations, "upgrade_to_head", side_effect=RuntimeError("secret detail")
        ), patch.object(migrations.logger, "error") as error:
            with pytest.raises(RuntimeError):
                migrations.run_migrations_at_startup()

        assert error.call_args.args[1] == "RuntimeError"
