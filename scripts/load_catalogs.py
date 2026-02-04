import json
import logging
import os
import sys

# --- Path Configuration ---
# Add the project root directory to sys.path to allow importing the 'app' module.
# This ensures the script runs correctly even if executed from inside the 'scripts/' folder.
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import DiagnosisCIE10, VaccineCVX

# --- Logging Configuration ---
# Standard output logging for tracking the seeding process
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CatalogLoader")

def load_cie10(db: Session):
    """
    Bulk loads ICD-10 (CIE-10) diagnoses from a local JSON file into the database.
    
    Strategy:
    1. Locate the raw JSON file in 'app/data/'.
    2. Wipe the existing table to prevent duplicates (Full Refresh).
    3. Bulk insert the new data.
    """
    # Dynamic path resolution to find the data file relative to this script
    file_path = os.path.join(os.path.dirname(__file__), '../app/data/cie10.json')
    
    if not os.path.exists(file_path):
        logger.error(f"❌ File not found: {file_path}")
        logger.error("Please ensure 'cie10.json' is placed inside 'app/data/'.")
        return

    logger.info(f"📖 Reading source file: {file_path}...")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        logger.info(f"Parsing {len(raw_data)} records...")

        diagnoses_to_insert = []
        # List of codes considered 'Common' in the border health context
        # These will appear at the top of the UI list if sorted by 'is_common'
        common_codes = {"A09", "J00", "E40", "J06.9", "B82.9"}

        for item in raw_data:
            code = item.get('code')
            desc = item.get('description')
            
            if code and desc:
                is_common = code in common_codes
                # Create the ORM object
                diagnoses_to_insert.append(
                    DiagnosisCIE10(code=str(code), description=desc, is_common=is_common)
                )

        # Atomic Transaction: Delete old data and insert new data in one go.
        logger.info("🗑️  Cleaning previous CIE-10 records...")
        db.query(DiagnosisCIE10).delete()
        
        logger.info(f"🚀 Bulk inserting {len(diagnoses_to_insert)} diagnoses...")
        db.add_all(diagnoses_to_insert)
        db.commit()
        logger.info("✅ CIE-10 Catalog loaded successfully.")

    except Exception as e:
        logger.error(f"Critical error loading CIE-10: {e}")
        db.rollback() # Rollback changes to keep DB in a consistent state

def load_vaccines(db: Session):
    """
    Loads the standard CVX (Vaccine Administered) codes.
    Source: CDC HL7 Table 0292.
    """
    # We use String for codes to preserve leading zeros if necessary (e.g., '03')
    vaccines = [
        VaccineCVX(code="90707", name="MMR (Measles, Mumps, Rubella)"),
        VaccineCVX(code="90700", name="DTaP (Diphtheria, Tetanus, Pertussis)"),
        VaccineCVX(code="90713", name="IPV (Inactivated Polio)"),
        VaccineCVX(code="90716", name="Varicella (Chickenpox)"),
        VaccineCVX(code="90723", name="DTaP-HepB-IPV (Pentavalent)"),
        VaccineCVX(code="90681", name="Rotavirus"),
        VaccineCVX(code="90670", name="PCV13 (Pneumococcal Conjugate)"),
        VaccineCVX(code="90717", name="Yellow Fever"),
        VaccineCVX(code="90746", name="Hepatitis B"),
        VaccineCVX(code="90712", name="OPV (Oral Polio)"),
        # Add more CVX codes here as required by UNICEF guidelines
    ]
    
    try:
        logger.info("🗑️  Cleaning previous Vaccine records...")
        db.query(VaccineCVX).delete()
        
        logger.info(f"🚀 Bulk inserting {len(vaccines)} vaccines...")
        db.add_all(vaccines)
        db.commit()
        logger.info("✅ Vaccine Catalog loaded successfully.")
    except Exception as e:
        logger.error(f"Critical error loading Vaccines: {e}")
        db.rollback()

def main():
    """
    Main entry point for the seeding script.
    Creates a dedicated database session and triggers loaders.
    """
    logger.info("--- Starting Catalog Seeding Process ---")
    
    # Create a new session specifically for this script
    db = SessionLocal()
    try:
        load_cie10(db)
        load_vaccines(db)
    finally:
        db.close()
        logger.info("--- Seeding Process Finished ---")

if __name__ == "__main__":
    main()