import logging
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.models import User
from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.catalog import CatalogSyncResponse
from app.services.catalog_service import get_all_diagnoses, get_all_vaccines, initialize_catalogs

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/sync", response_model=CatalogSyncResponse, status_code=status.HTTP_200_OK)
def sync_catalogs(
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """
    Get the full catalog of Diagnoses (CIE-10) and Vaccines (CVX).
    
    Use Case:
    - Frontend calls this on startup to populate dropdown menus locally.
    - Ensures offline availability of standardized medical codes.
    """
    logger.info(f"Catalogs requested by User {current_user.email}")
    
    # 1. Ensure DB has data (Auto-Seeding for development convenience)
    initialize_catalogs(db)
    
    # 2. Fetch data
    diagnoses = get_all_diagnoses(db)
    vaccines = get_all_vaccines(db)
    
    logger.info(f"Returning {len(diagnoses)} diagnoses and {len(vaccines)} vaccines.")

    return CatalogSyncResponse(
        diagnoses=diagnoses,
        vaccines=vaccines,
        version="v1.0" # Hardcoded version for now
    )