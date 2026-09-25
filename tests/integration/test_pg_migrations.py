"""
Migrations on PostgreSQL (audit be-v2-esquema-sin-migraciones): run in a
scratch database next to the test one, so the other tests are not disturbed.
"""
import threading
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.db import migrations
from app.db.base import Base
from app.db.schema_check import schema_drift


@pytest.fixture
def scratch_engine(pg_engine):
    name = f"hwb_test_scratch_{uuid.uuid4().hex[:8]}"
    admin = create_engine(pg_engine.url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(pg_engine.url.set(database=name))
    yield engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def _version(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_migrations_build_the_model_schema_and_nothing_else(scratch_engine):
    migrations.upgrade_to_head(scratch_engine)

    with scratch_engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == []
    assert not schema_drift(scratch_engine).blocking


def test_every_migration_downgrades_and_upgrades_again(scratch_engine):
    migrations.upgrade_to_head(scratch_engine)
    head = _version(scratch_engine)
    config = migrations.alembic_config()

    with scratch_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "0001")
    assert _version(scratch_engine) == "0001"

    migrations.upgrade_to_head(scratch_engine)
    assert _version(scratch_engine) == head


def test_concurrent_instances_migrate_once(scratch_engine):
    """The advisory lock: several Cloud Run instances starting together."""
    errors = []
    barrier = threading.Barrier(3)

    def instance():
        try:
            barrier.wait()
            migrations.upgrade_to_head(scratch_engine)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=instance) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with scratch_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1
