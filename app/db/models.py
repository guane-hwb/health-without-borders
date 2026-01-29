from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Text, JSON
from sqlalchemy.sql import func
from app.db.base import Base
from sqlalchemy import Float


class Patient(Base):
    __tablename__ = "patients"

    # ID Interno (ej. "UNICEF-COL-84321")
    id = Column(String, primary_key=True, index=True)
    
    # ID del chip NFC
    nfc_uid = Column(String, unique=True, index=True, nullable=False)
    
    # Datos Demográficos Básicos (Para búsquedas rápidas en SQL)
    first_name = Column(String, index=True)
    last_name = Column(String, index=True)
    birth_date = Column(Date)
    blood_type = Column(String(5))
    
    weight = Column(Float, nullable=True)
    height = Column(Float, nullable=True)

    # Guardamos el JSON completo crudo para tener todo el historial clínico 
    # sin complicar la estructura relacional por ahora (Patrón Híbrido)
    full_record_json = Column(JSON) 
    
    # Metadatos de auditoría
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_synced_with_cloud = Column(Boolean, default=False)

# ---------------------------------------------------------
# CATÁLOGO: DIAGNÓSTICOS (CIE-10)
# ---------------------------------------------------------
class DiagnosisCIE10(Base):
    __tablename__ = "catalog_cie10"

    # El código oficial (ej. "A09.9")
    code = Column(String(10), primary_key=True, index=True)
    
    # Descripción (ej. "Gastroenteritis y colitis...")
    description = Column(Text, nullable=False)
    
    # Para optimización: Marcar las enfermedades más comunes en frontera
    is_common = Column(Boolean, default=False)

# ---------------------------------------------------------
# CATÁLOGO: VACUNAS (CVX - HL7)
# ---------------------------------------------------------
class VaccineCVX(Base):
    __tablename__ = "catalog_vaccines"

    # Código numérico CVX (ej. 90707)
    code = Column(Integer, primary_key=True, index=True)
    
    # Nombre de la vacuna (ej. "Triple Viral")
    name = Column(String, nullable=False)
    
    is_active = Column(Boolean, default=True)