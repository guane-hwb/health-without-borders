import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import get_password_hash
from app.db.models import User, UserRole
from app.db.session import get_db
from app.schemas.user import UserCreate, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_in: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new user in the system.

    **Role restrictions:**
    - `superadmin`: Can create users of any role in any organization.
    Must provide `organization_id` in the request body.
    - `org_admin`: Can only create `doctor` or `nurse` accounts.
    The new user is automatically assigned to the caller's organization,
    regardless of any `organization_id` provided in the body.

    **Responses:**
    - `201`: User created successfully.
    - `400`: A user with that email already exists.
    - `400`: `superadmin` did not provide `organization_id`.
    - `403`: Caller is a `doctor` or `nurse`.
    - `403`: `org_admin` attempted to create an `org_admin` or `superadmin`.
    """
    logger.info(f"User {current_user.email} (Role: {current_user.role}) is attempting to create a new user.")

    # 1. Authorization Check (Only Admins allowed)
    if current_user.role not in {UserRole.superadmin, UserRole.org_admin}:
        logger.warning(f"Unauthorized creation attempt by {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges to create users."
        )

    # 2. Role Restriction for Org Admins
    if current_user.role == UserRole.org_admin and user_in.role not in [UserRole.doctor, UserRole.nurse]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization Admins can only create 'doctor' or 'nurse' accounts."
        )

    # 3. Check if email already exists globally
    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The user with this email already exists in the system."
        )

    ## 4. Enforce Multi-Tenancy based on Role
    if current_user.role == UserRole.superadmin:
        # SuperAdmin can create users for any organization, but must specify the target organization
        if not user_in.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SuperAdmins must provide an 'organization_id' when creating users."
            )
        target_org_id = user_in.organization_id
    else:
        # This ensures that org_admins can only create users within their own organization, regardless of the input.
        target_org_id = current_user.organization_id

    # 5. Create the DB User object
    db_user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        hashed_password=get_password_hash(user_in.password),
        role=user_in.role,
        is_active=user_in.is_active,
        organization_id=target_org_id
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    logger.info(f"User {db_user.email} created successfully in Org {target_org_id}.")
    
    return db_user

@router.get("/", response_model=List[UserResponse])
def get_users_by_organization(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all users belonging to the caller's organization.

    - `superadmin`: Returns all users across all organizations.
    - `org_admin`: Returns only users within their own organization.
    - `doctor` / `nurse`: Access denied.

    **Responses:**
    - `200`: List of users.
    - `403`: Caller is a `doctor` or `nurse`.
    """
    # 1. Authorization
    if current_user.role not in {UserRole.superadmin, UserRole.org_admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges to view user lists."
        )

    # 2. Strict Multi-Tenant Query
    if current_user.role == UserRole.superadmin:
        users = db.query(User).all()
    else:
        users = db.query(User).filter(
            User.organization_id == current_user.organization_id
        ).all()
    
    return users

@router.get("/me", response_model=UserResponse)
def get_current_user_profile(current_user: User = Depends(get_current_user)):
    return current_user