from typing import List

from pydantic import BaseModel


# --- ICD-10 (Diagnoses) ---
class DiagnosisBase(BaseModel):
    code: str
    description: str
    is_common: bool

    class Config:
        from_attributes = True

# --- CVX (Vaccines) ---
class VaccineBase(BaseModel):
    code: str
    name: str
    is_active: bool

    class Config:
        from_attributes = True

# --- Sync Response ---
# Large payload downloaded when Wi-Fi is available
class CatalogSyncResponse(BaseModel):
    diagnoses: List[DiagnosisBase]
    vaccines: List[VaccineBase]
    version: str = "v1" # Used to check for updates