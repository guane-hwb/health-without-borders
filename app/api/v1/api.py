from fastapi import APIRouter
from app.api.v1.endpoints import patients
# from app.api.v1.endpoints import catalogs # Descomentar cuando movamos catálogos

api_router = APIRouter()

# Include the patients router
api_router.include_router(patients.router, prefix="/patients", tags=["Patients"])

# Future: api_router.include_router(catalogs.router, prefix="/catalogs", tags=["Catalogs"])