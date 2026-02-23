from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import date

# --- Sub-models (Nested Objects) ---

class Address(BaseModel):
    street: str
    city: str
    state: str
    zipCode: str
    country: str

class PatientInfo(BaseModel):
    lastName: str
    firstName: str
    dob: date # Date of Birth
    gender: str
    bloodType: str
    address: Address
    
    # Optional fields (Frontend might not send them initially)
    allergies: Optional[str] = "NINGUNA" 
    weight: Optional[float] = Field(None, description="Weight in Kg (e.g. 12.5)")
    height: Optional[float] = Field(None, description="Height in cm (e.g. 95.0)")

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
    vaccineCode: str  # CVX Code (e.g., "90707")
    dose: int
    lotNumber: str
    status: str

# --- Main Model (Incoming JSON Payload) ---

class PatientFullRecord(BaseModel):
    patientId: str
    device_uid: str = Field(..., description="Unique identifier from the hardware tag (NFC UID, QR code string, etc.)")
    patientInfo: PatientInfo
    guardianInfo: GuardianInfo
    medicalHistory: List[MedicalHistoryItem] = []
    vaccinationRecord: List[VaccinationRecordItem] = []

    class Config:
        from_attributes = True

# --- API Responses ---

class PatientSyncResponse(BaseModel):
    status: str
    internal_id: str
    gcp_status: Optional[str] = "unknown" # Added to match endpoint logic
    message: str