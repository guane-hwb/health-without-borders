import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DB_Setup")

def init_db():
    logger.info("Building database schema...")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Tables verified/created successfully!")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")

if __name__ == "__main__":
    init_db()