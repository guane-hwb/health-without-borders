"""
Bring the database schema up to date: ``alembic upgrade head``.

Kept under its historical name because the deployment docs call it. The
service already migrates itself at startup (app/db/migrations.py); this script
is for local development or a one-off run against a database the service has
not started on yet.
"""
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.migrations import upgrade_to_head  # noqa: E402
from app.db.session import engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DB_Setup")


def init_db() -> bool:
    logger.info("Applying database migrations...")
    try:
        upgrade_to_head(engine)
        logger.info("Database schema is at the latest migration.")
        return True
    except Exception as e:
        # Only the type: driver messages can embed statement data.
        logger.error("Migration failed: %s", type(e).__name__)
        return False


if __name__ == "__main__":
    # A non-zero exit lets a pipeline notice the schema was not built.
    sys.exit(0 if init_db() else 1)
