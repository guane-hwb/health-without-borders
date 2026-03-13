import logging
from sqlalchemy.orm import Session
from app.db.models import DiagnosisCIE10, VaccineCVX

logger = logging.getLogger(__name__)

def get_all_diagnoses(db: Session):
    """Retrieves all available diagnoses from the catalog."""
    return db.query(DiagnosisCIE10).all()

def get_all_vaccines(db: Session):
    """Retrieves all available vaccines from the catalog."""
    return db.query(VaccineCVX).filter(VaccineCVX.is_active == True).all()