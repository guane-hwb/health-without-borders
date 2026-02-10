from fastapi import APIRouter
from app.api.v1.endpoints import patients, catalogs, login

api_router = APIRouter()

api_router.include_router(login.router, tags=["Login"])
api_router.include_router(patients.router, prefix="/patients", tags=["Patients"])
api_router.include_router(catalogs.router, prefix="/catalogs", tags=["Catalogs"])