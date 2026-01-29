from pydantic import BaseModel
from typing import List, Optional
from datetime import date

# --- Sub-modelos (Para los objetos anidados) ---

class Address(BaseModel):
    street: str
    city: str
    state: str
    zipCode: str
    country: str

class PatientInfo(BaseModel):
    lastName: str
    firstName: str
    dob: date # date of birth
    gender: str
    bloodType: str
    address: Address
    # Agregamos alergias aquí aunque no venga en el JSON original, 
    # para que el Front sepa que lo esperamos a futuro.
    allergies: Optional[str] = "NINGUNA" 
    weight: Optional[float] = None  # En Kilogramos (ej. 12.5)
    height: Optional[float] = None  # En Centímetros (ej. 95.0)

class GuardianInfo(BaseModel):
    name: str
    relationship: str
    phone: str

class DiagnosisData(BaseModel):
    icd10Code: str
    description: str

class MedicalHistoryItem(BaseModel):
    type: str
    date: date
    location: str
    physician: str
    diagnosis: DiagnosisData
    observations: Optional[str] = None

class VaccinationRecordItem(BaseModel):
    date: date
    vaccineName: str
    vaccineCode: str  # El código CVX (ej. "90707")
    dose: int
    lotNumber: str
    status: str

# --- Modelo Principal (El JSON completo) ---

class PatientFullRecord(BaseModel):
    patientId: str
    patientInfo: PatientInfo
    guardianInfo: GuardianInfo
    medicalHistory: List[MedicalHistoryItem] = []
    vaccinationRecord: List[VaccinationRecordItem] = []

    class Config:
        from_attributes = True


class PatientSyncResponse(BaseModel):
    status: str
    internal_id: str
    message: str