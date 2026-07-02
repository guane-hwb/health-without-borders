import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import get_password_hash
from app.db.models import Organization, Patient, User, UserRole
from app.db.session import get_db
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _org_to_response(db: Session, org: Organization) -> OrganizationResponse:
    """Build an OrganizationResponse enriched with live user/patient counts."""
    user_count = (
        db.query(func.count(User.id))
        .filter(User.organization_id == org.id)
        .scalar()
    ) or 0
    patient_count = (
        db.query(func.count(Patient.id))
        .filter(Patient.organization_id == org.id)
        .scalar()
    ) or 0
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        is_active=org.is_active,
        user_count=user_count,
        patient_count=patient_count,
    )

@router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_organization(
    org_in: OrganizationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new Organization (tenant), optionally provisioning its initial
    org_admin in the SAME transaction.

    An Organization is the root isolation boundary. All users and patients belong
    to exactly one organization, ensuring complete data separation between tenants.

    When `admin` is provided, the organization and its administrator are created
    atomically: if the admin cannot be created (e.g. the email already exists),
    the whole operation is rolled back and no organization is persisted. This
    prevents orphan organizations that have no administrator.

    - **Allowed roles:** `superadmin` only.
    - **Responses:**
    - `201`: Organization (and admin, if requested) created successfully.
    - `400`: Organization name already exists, or admin email already exists.
    - `403`: Caller is not a `superadmin`.
    """
    if current_user.role != UserRole.superadmin:
        logger.warning(f"Unauthorized organization creation attempt by actor_id={current_user.id}")
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

    # Fail fast on a duplicate admin email BEFORE inserting anything, so the
    # caller gets a clean 400 and no partial state is created.
    if org_in.admin is not None:
        existing_user = db.query(User).filter(User.email == org_in.admin.email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with the administrator email already exists in the system."
            )

    new_org = Organization(name=org_in.name, is_active=org_in.is_active)
    db.add(new_org)

    try:
        # Flush (not commit) to assign the organization PK so the admin can be
        # attached to the very same uncommitted transaction.
        db.flush()

        if org_in.admin is not None:
            admin_user = User(
                email=org_in.admin.email,
                full_name=org_in.admin.full_name,
                hashed_password=get_password_hash(org_in.admin.password),
                role=UserRole.org_admin,
                is_active=True,
                organization_id=new_org.id,
            )
            db.add(admin_user)

        # Single commit: org + admin succeed or fail together.
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to provision organization+admin actor_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create the organization."
        )

    db.refresh(new_org)
    logger.info(
        "✅ SuperAdmin actor_id=%s created Organization %s (ID: %s)%s",
        current_user.id, new_org.name, new_org.id,
        " with initial admin" if org_in.admin is not None else "",
    )
    return _org_to_response(db, new_org)

@router.get("/", response_model=List[OrganizationResponse])
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List organizations visible to the current user.

    Visibility is strictly scoped by role:
    - `superadmin`: Returns all organizations in the system.
    - `org_admin`: Returns only their own organization.
    - `doctor` / `nurse`: Access denied.

    - **Responses:**
    - `200`: List of organizations (may contain 1 or many depending on role).
    - `403`: Caller is a `doctor` or `nurse`.
    """
    if current_user.role == UserRole.superadmin:
        orgs = db.query(Organization).all()
    elif current_user.role == UserRole.org_admin:
        orgs = db.query(Organization).filter(
            Organization.id == current_user.organization_id
        ).all()
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view organizations."
        )

    # Aggregate membership counts in two grouped queries to avoid N+1 lookups.
    user_counts = dict(
        db.query(User.organization_id, func.count(User.id))
        .group_by(User.organization_id)
        .all()
    )
    patient_counts = dict(
        db.query(Patient.organization_id, func.count(Patient.id))
        .group_by(Patient.organization_id)
        .all()
    )
    return [
        OrganizationResponse(
            id=o.id,
            name=o.name,
            is_active=o.is_active,
            user_count=user_counts.get(o.id, 0),
            patient_count=patient_counts.get(o.id, 0),
        )
        for o in orgs
    ]


@router.patch("/{org_id}", response_model=OrganizationResponse)
def update_organization_status(
    org_id: str,
    org_in: OrganizationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Activate or deactivate an organization (soft state change).

    Deactivating is the safe default for retiring an organization: it preserves
    all users, patients and clinical history while blocking access.

    - **Allowed roles:** `superadmin` only.
    - **Guards:** a superadmin cannot deactivate their own organization.
    - **Responses:**
    - `200`: Updated organization.
    - `400`: Attempt to deactivate own organization.
    - `403`: Caller is not a `superadmin`.
    - `404`: Organization not found.
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only global SuperAdmins can change an organization's state.",
        )

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    if org.id == current_user.organization_id and org_in.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own organization.",
        )

    org.is_active = org_in.is_active
    db.commit()
    db.refresh(org)
    logger.info(
        "SuperAdmin actor_id=%s set Organization %s active=%s",
        current_user.id, org.id, org.is_active,
    )
    return _org_to_response(db, org)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(
    org_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete an organization (hard delete).

    Permitted only when the organization has NO patients, because clinical
    records are irreversible and must never be destroyed by an account-management
    action. Its user accounts (admins and staff), which are recreable, are
    removed in the SAME transaction so that an organization provisioned with an
    initial admin can still be torn down. For any organization that has patients,
    deactivate it instead (see PATCH).

    - **Allowed roles:** `superadmin` only.
    - **Guards:** cannot delete your own organization; cannot delete one that
      still has patients.
    - **Responses:**
    - `204`: Organization (and its users) deleted.
    - `403`: Caller is not a `superadmin`, or is deleting their own organization.
    - `404`: Organization not found.
    - `409`: Organization still has patients.
    """
    if current_user.role != UserRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only global SuperAdmins can delete organizations.",
        )

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    if org.id == current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete your own organization.",
        )

    patient_count = (
        db.query(func.count(Patient.id)).filter(Patient.organization_id == org.id).scalar()
    ) or 0

    if patient_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Organization has {patient_count} patient(s). Clinical records "
                f"cannot be deleted — deactivate the organization instead."
            ),
        )

    try:
        # Cascade: remove the organization's user accounts first (they FK to the
        # organization), then the organization itself — one atomic transaction.
        deleted_users = (
            db.query(User)
            .filter(User.organization_id == org.id)
            .delete(synchronize_session=False)
        )
        db.delete(org)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to delete Organization %s actor_id=%s", org_id, current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete the organization.",
        )

    logger.info(
        "SuperAdmin actor_id=%s hard-deleted Organization %s (cascaded %s user(s))",
        current_user.id, org_id, deleted_users,
    )
    return None