from fastapi import APIRouter

from app.api.v1.endpoints import login, organizations, patients, stats, users

api_router = APIRouter()

api_router.include_router(login.router, tags=["Login"])
api_router.include_router(patients.router, prefix="/patients", tags=["Patients"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(organizations.router, prefix="/organizations", tags=["Organizations"])
api_router.include_router(stats.router, prefix="/stats", tags=["Statistics"])