import json
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import DiagnosisCIE10, VaccineCVX

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CatalogLoader")

def load_cie10(db: Session):
    """
    Bulk loads ICD-10 (CIE-10) using High-Performance Core inserts.
    """
    file_path = os.path.join(os.path.dirname(__file__), '../app/data/cie10.json')
    
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return

    logger.info(f"Reading source file: {file_path}...")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        logger.info(f"Parsing {len(raw_data)} records...")

        # We will create a list of dictionaries to bulk insert. We also add a flag for common diagnoses for potential future use in the UI.
        diagnoses_mappings = []
        common_codes = {"A09", "J00", "E40", "J06.9", "B82.9"}

        for item in raw_data:
            code = item.get('code')
            desc = item.get('description')
            
            if code and desc:
                diagnoses_mappings.append({
                    "code": str(code),
                    "description": desc,
                    "is_common": code in common_codes
                })

        logger.info("🗑️ Cleaning previous CIE-10 records...")
        db.query(DiagnosisCIE10).delete()
        
        logger.info(f"Bulk inserting {len(diagnoses_mappings)} diagnoses. Hold tight...")
        db.bulk_insert_mappings(DiagnosisCIE10, diagnoses_mappings)
        db.commit()
        
        logger.info("CIE-10 Catalog loaded successfully.")

    except Exception as e:
        logger.error(f"Critical error loading CIE-10: {e}")
        db.rollback()

def load_vaccines(db: Session):
    """
    Loads the standard CVX (Vaccine Administered) codes.
    """
    vaccines_mappings = [
        {"code": "90707", "name": "MMR (Measles, Mumps, Rubella)", "is_active": True},
        {"code": "90700", "name": "DTaP (Diphtheria, Tetanus, Pertussis)", "is_active": True},
        {"code": "90713", "name": "IPV (Inactivated Polio)", "is_active": True},
        {"code": "90716", "name": "Varicella (Chickenpox)", "is_active": True},
        {"code": "90723", "name": "DTaP-HepB-IPV (Pentavalent)", "is_active": True},
        {"code": "90681", "name": "Rotavirus", "is_active": True},
        {"code": "90670", "name": "PCV13 (Pneumococcal Conjugate)", "is_active": True},
        {"code": "90717", "name": "Yellow Fever", "is_active": True},
        {"code": "90746", "name": "Hepatitis B", "is_active": True},
        {"code": "90712", "name": "OPV (Oral Polio)", "is_active": True},
    ]
    
    try:
        logger.info("Cleaning previous Vaccine records...")
        db.query(VaccineCVX).delete()
        
        logger.info(f"Bulk inserting {len(vaccines_mappings)} vaccines...")
        db.bulk_insert_mappings(VaccineCVX, vaccines_mappings)
        db.commit()
        logger.info("Vaccine Catalog loaded successfully.")
    except Exception as e:
        logger.error(f"Critical error loading Vaccines: {e}")
        db.rollback()

def main():
    logger.info("--- Starting Catalog Seeding Process ---")
    db = SessionLocal()
    try:
        load_cie10(db)
        load_vaccines(db)
    finally:
        db.close()
        logger.info("--- Seeding Process Finished ---")

if __name__ == "__main__":
    main()