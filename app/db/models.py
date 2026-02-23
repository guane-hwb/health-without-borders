from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, Text, JSON, Float
from sqlalchemy.sql import func
from app.db.base import Base

class Patient(Base):
    """
    Main Patient Entity.
    Stores both relational data for quick lookups and the raw JSON 
    for complete medical history (Hybrid Pattern).
    """
    __tablename__ = "patients"

    # Internal ID (e.g., "UNICEF-COL-84321")
    id = Column(String, primary_key=True, index=True)
    
    # Chip UID (Factory ID) - Critical for security
    device_uid = Column(String, unique=True, index=True, nullable=False)
    
    # Basic Demographics (Indexed for SQL search performance)
    first_name = Column(String, index=True)
    last_name = Column(String, index=True)
    birth_date = Column(Date)
    blood_type = Column(String(5))
    
    # Anthropometric Data (Critical for malnutrition tracking)
    weight = Column(Float, nullable=True) # kg
    height = Column(Float, nullable=True) # cm

    # Raw Full JSON Storage
    # Preserves the exact payload received from mobile for audit/HL7 generation
    full_record_json = Column(JSON) 
    
    # Audit Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_synced_with_cloud = Column(Boolean, default=False)

    def __repr__(self):
        return f"<Patient(id={self.id}, name={self.first_name} {self.last_name})>"

# ---------------------------------------------------------
# CATALOG: DIAGNOSES (ICD-10 / CIE-10)
# ---------------------------------------------------------
class DiagnosisCIE10(Base):
    __tablename__ = "catalog_cie10"

    # Official Code (e.g., "A09.9")
    code = Column(String(10), primary_key=True, index=True)
    
    # Description (e.g., "Gastroenteritis...")
    description = Column(Text, nullable=False)
    
    # Optimization: Flag for common diseases in border areas
    is_common = Column(Boolean, default=False)

# ---------------------------------------------------------
# CATALOG: VACCINES (CVX - HL7 Standard)
# ---------------------------------------------------------
class VaccineCVX(Base):
    __tablename__ = "catalog_vaccines"

    # CVX Numeric Code (e.g., 90707)
    code = Column(String(10), primary_key=True, index=True)
    
    # Vaccine Name (e.g., "MMR")
    name = Column(String, nullable=False)
    
    is_active = Column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    
    hashed_password = Column(String, nullable=False)
    
    # Roles: "admin", "doctor", "nurse"
    role = Column(String, default="doctor")
    
    is_active = Column(Boolean, default=True)