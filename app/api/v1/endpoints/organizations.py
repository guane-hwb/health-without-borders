import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Organization, User
from app.schemas.organization import OrganizationCreate, OrganizationResponse
from app.api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_organization(
    org_in: OrganizationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new Organization (Tenant).
    STRICT SECURITY: Only SuperAdmins can execute this action.
    """
    if current_user.role != "superadmin":
        logger.warning(f"Unauthorized organization creation attempt by {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only global SuperAdmins can create new organizations."
        )

    # Validate that the organization name is unique across the entire database
    existing_org = db.query(Organization).filter(Organization.name == org_in.name).first()
    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The organization '{org_in.name}' already exists."
        )

    # Create the new organization
    new_org = Organization(
        name=org_in.name,
        is_active=org_in.is_active
    )
    db.add(new_org)
    db.commit()
    db.refresh(new_org)
    
    logger.info(f"✅ SuperAdmin {current_user.email} created new Organization: {new_org.name} (ID: {new_org.id})")
    
    return new_org

@router.get("/", response_model=List[OrganizationResponse])
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List organizations.
    - superadmin: Sees all organizations in the database.
    - org_admin: Sees ONLY their own organization.
    - doctor/nurse: Access Denied.
    """
    if current_user.role == "superadmin":
        return db.query(Organization).all()
    
    elif current_user.role == "org_admin":
        return db.query(Organization).filter(Organization.id == current_user.organization_id).all()
    
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view organizations."
        )