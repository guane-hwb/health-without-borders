from pydantic import BaseModel
from typing import List

# --- CIE-10 (Diagnósticos) ---
class DiagnosisBase(BaseModel):
    code: str
    description: str
    is_common: bool

    class Config:
        from_attributes = True

# --- CVX (Vacunas) ---
class VaccineBase(BaseModel):
    code: int
    name: str
    is_active: bool

    class Config:
        from_attributes = True

# --- Respuesta de Sincronización ---
# Este es el "Paquete Gigante" que se descarga cuando hay Wi-Fi
class CatalogSyncResponse(BaseModel):
    diagnoses: List[DiagnosisBase]
    vaccines: List[VaccineBase]
    version: str = "v1" # Útil para saber si necesita actualizar