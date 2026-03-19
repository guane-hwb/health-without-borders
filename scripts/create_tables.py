import logging
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.db.session import engine
from app.db.base import Base
import app.db.models  # noqa: F401

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