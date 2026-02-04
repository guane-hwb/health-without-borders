# create_tables.py
import logging
from app.db.session import engine
from app.db.base import Base
import sys
import os
sys.path.append(os.getcwd())

# IMPORTANTE: Debemos importar los modelos para que SQLAlchemy los reconozca
# antes de llamar a create_all. Si no se importan, no se crean.
from app.db.models import Patient, DiagnosisCIE10, VaccineCVX

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    logger.info("Creating database tables...")
    try:
        # Esto crea las tablas SOLO si no existen.
        Base.metadata.create_all(bind=engine)
        logger.info("Tables created successfully!")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")

if __name__ == "__main__":
    init_db()