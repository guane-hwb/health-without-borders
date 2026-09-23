import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DB_Setup")

def init_db() -> bool:
    logger.info("Building database schema...")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Tables verified/created successfully!")
        return True
    except Exception as e:
        # Only the type: driver messages can embed statement data.
        logger.error("Error creating tables: %s", type(e).__name__)
        return False

if __name__ == "__main__":
    # A non-zero exit lets a pipeline notice the schema was not built.
    sys.exit(0 if init_db() else 1)