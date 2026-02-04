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

def initialize_catalogs(db: Session):
    """
    Seeds the database with initial data if empty.
    Crucial for the first run of the application.
    """
    # Check if we have diagnoses
    if db.query(DiagnosisCIE10).count() == 0:
        logger.info("Seeding initial CIE-10 Diagnoses...")
        # A small sample of common border health issues
        initial_dx = [
            DiagnosisCIE10(code="A09", description="Diarrhea and gastroenteritis of presumed infectious origin", is_common=True),
            DiagnosisCIE10(code="J00", description="Acute nasopharyngitis [common cold]", is_common=True),
            DiagnosisCIE10(code="J06.9", description="Acute upper respiratory infection, unspecified", is_common=True),
            DiagnosisCIE10(code="E46", description="Unspecified protein-energy malnutrition", is_common=True),
            DiagnosisCIE10(code="B82.9", description="Intestinal parasitism, unspecified", is_common=False),
        ]
        db.add_all(initial_dx)
        db.commit()

    # Check if we have vaccines
    if db.query(VaccineCVX).count() == 0:
        logger.info("Seeding initial CVX Vaccines...")
        # Common vaccines in Colombia/Venezuela border context
        initial_vac = [
            VaccineCVX(code="90707", name="MMR (Measles, Mumps, Rubella)"),
            VaccineCVX(code="90700", name="DTaP (Diphtheria, Tetanus, Pertussis)"),
            VaccineCVX(code="90713", name="Polio (IPV)"),
            VaccineCVX(code="90716", name="Varicella (Chickenpox)"),
            VaccineCVX(code="90723", name="DTaP-HepB-IPV"),
        ]
        db.add_all(initial_vac)
        db.commit()