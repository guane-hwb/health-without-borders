import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import get_password_hash, nfc_key_claims
from app.db.models import User, UserRole
from app.db.session import get_db
from app.schemas.user import UserCreate, UserResponse, UserUpdate

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
    logger.info(f"User creation attempt by actor_id={current_user.id} (Role: {current_user.role}).")

    # 1. Authorization Check (Only Admins allowed)
    if current_user.role not in {UserRole.superadmin, UserRole.org_admin}:
        logger.warning(f"Unauthorized user creation attempt by actor_id={current_user.id}")
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
    
    logger.info(f"User user_id={db_user.id} created successfully in Org {target_org_id}.")
    
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
    """Return the current user's profile, including the NFC keyring so the
    device can repopulate its in-memory key after a cold start."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        organization_id=current_user.organization_id,
        **nfc_key_claims(role=current_user.role),
    )


def _load_manageable_target(
    user_id: str, db: Session, current_user: User, action: str
) -> User:
    """
    Fetch the target user and enforce shared management guards used by both
    the PATCH (deactivate) and DELETE endpoints.

    Guards:
      - Only `superadmin` / `org_admin` may manage users.
      - Nobody may act on their own account (prevents self lock-out).
      - `org_admin` may only act on `doctor` / `nurse` in their OWN organization.
      - `superadmin` accounts can never be targeted through this API.
    """
    if current_user.role not in {UserRole.superadmin, UserRole.org_admin}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges to manage users.",
        )

    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if target.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You cannot {action} your own account.",
        )

    if target.role == UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SuperAdmin accounts cannot be managed through this endpoint.",
        )

    if current_user.role == UserRole.org_admin:
        if target.organization_id != current_user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only manage users within your own organization.",
            )
        if target.role not in {UserRole.doctor, UserRole.nurse}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization Admins can only manage 'doctor' or 'nurse' accounts.",
            )

    return target


@router.patch("/{user_id}", response_model=UserResponse)
def update_user_status(
    user_id: str,
    user_in: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Activate or deactivate a user (soft state change).

    Deactivating is the safe default for revoking access: the account and its
    audit trail are preserved while login and API access are blocked.

    - **Allowed roles:** `superadmin`, `org_admin` (scoped to own org / clinical roles).
    - **Responses:**
    - `200`: Updated user.
    - `400`: Attempt to change own account.
    - `403`: Not enough privileges, cross-org, or targeting a superadmin/admin.
    - `404`: User not found.
    """
    target = _load_manageable_target(user_id, db, current_user, action="deactivate")
    target.is_active = user_in.is_active
    db.commit()
    db.refresh(target)
    logger.info(
        "actor_id=%s set user_id=%s active=%s",
        current_user.id, target.id, target.is_active,
    )
    return target


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete a user (hard delete).

    - **Allowed roles:** `superadmin`, `org_admin` (scoped to own org / clinical roles).
    - **Guards:** cannot delete your own account; cannot delete a superadmin;
      cannot delete the LAST `org_admin` of an organization (which would leave
      it unmanageable — deactivate or delete the organization instead).
    - **Responses:**
    - `204`: User deleted.
    - `400`: Attempt to delete own account.
    - `403`: Not enough privileges, cross-org, or targeting a superadmin/admin.
    - `404`: User not found.
    - `409`: Target is the last administrator of its organization.
    """
    target = _load_manageable_target(user_id, db, current_user, action="delete")

    # Last-admin guard: only a superadmin can reach an org_admin here, and we
    # must not strip an organization of its only administrator.
    if target.role == UserRole.org_admin:
        other_admins = (
            db.query(func.count(User.id))
            .filter(
                User.organization_id == target.organization_id,
                User.role == UserRole.org_admin,
                User.id != target.id,
            )
            .scalar()
        ) or 0
        if other_admins == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot delete the last administrator of an organization. "
                    "Provision another admin first, or delete the organization."
                ),
            )

    db.delete(target)
    db.commit()
    logger.info("actor_id=%s hard-deleted user_id=%s", current_user.id, user_id)
    return None