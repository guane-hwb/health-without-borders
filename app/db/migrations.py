"""
Apply database migrations (Alembic) when the service starts.

Cloud Run already reaches Cloud SQL, so the container migrates the schema
itself: no public IP on the database and no manual step from a laptop. A
PostgreSQL advisory lock makes concurrent instances wait for each other
instead of racing, and a failure raises so the new revision does not start —
Cloud Run then keeps serving the previous one.

Audit finding: be-v2-esquema-sin-migraciones (step A.2).
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: Arbitrary constant shared by every instance: the key of the advisory lock.
MIGRATION_LOCK_KEY = 804_117_2026


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # Keep the app's logging configuration (PHI-safe formatter) intact.
    config.attributes["skip_logging_config"] = True
    return config


def upgrade_to_head(engine: Engine) -> None:
    """Bring the database to the latest revision, one instance at a time."""
    config = alembic_config()
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # Held until this transaction ends; other instances block here.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def run_migrations_at_startup() -> None:
    if not settings.RUN_MIGRATIONS_ON_STARTUP:
        logger.info("RUN_MIGRATIONS_ON_STARTUP is off; not migrating the database.")
        return

    from app.db.session import engine

    logger.info("Applying database migrations.")
    try:
        upgrade_to_head(engine)
    except Exception as exc:
        # Only the type: driver messages can embed statement data.
        logger.error("Database migration failed: %s", type(exc).__name__)
        raise
    logger.info("Database schema is at the latest migration.")
